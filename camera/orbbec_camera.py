"""Orbbec color/depth acquisition; SDK objects stay in this adapter."""

import threading
import time
from typing import NamedTuple, Optional

import cv2
import numpy as np
import pyorbbecsdk as ob

from core.logger import get_logger


logger = get_logger(__name__)


class FramePacket(NamedTuple):
    frame_id: int
    timestamp: float
    frame: np.ndarray
    depth_scale: Optional[float] = None


def discover_device_serial_numbers() -> list[str]:
    context = ob.Context()
    devices = context.query_devices()
    return [
        devices.get_device_serial_number_by_index(index)
        for index in range(devices.get_count())
    ]


class OrbbecCamera:
    """Capture the newest color BGR and/or raw uint16 depth frames.

    Returned arrays are shared: callers must not modify them in place.
    Depth in millimetres is raw_value * depth_scale.
    """

    def __init__(self, device_sn: str, stream_config: dict):
        self.device_sn = device_sn
        self.stream_config = stream_config
        stream_type = stream_config["type"]
        if stream_type not in ("color", "depth", "depth_and_color"):
            raise ValueError(f"不支持的 stream.type: {stream_type}")
        self.stream_types = (
            ("color", "depth")
            if stream_type == "depth_and_color" else (stream_type,)
        )
        self.align_mode = stream_config["align_mode"]
        if self.align_mode not in ("DISABLE", "HW_D2C", "SW_D2C"):
            raise ValueError(f"不支持的 stream.align_mode: {self.align_mode}")
        if self.align_mode != "DISABLE" and stream_type != "depth_and_color":
            raise ValueError("D2C 对齐要求同时开启 color 和 depth")

        self.context = None
        self.device = None
        self.pipeline = None
        self.config = None
        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pipeline_started = False
        self._frame_lock = threading.Lock()
        self._packets: dict[str, Optional[FramePacket]] = {
            name: None for name in self.stream_types
        }
        self._frame_ids = {name: 0 for name in self.stream_types}
        self._capture_error: Optional[BaseException] = None
        self._init_camera()

    def _init_camera(self) -> None:
        self.context = ob.Context()
        devices = self.context.query_devices()
        available_sn = [
            devices.get_device_serial_number_by_index(index)
            for index in range(devices.get_count())
        ]
        if self.device_sn not in available_sn:
            raise RuntimeError(
                f"没有找到相机 {self.device_sn}，当前设备: {available_sn}"
            )

        self.device = devices.get_device_by_serial_number(self.device_sn)
        info = self.device.get_device_info()
        logger.info(
            "Orbbec device selected: name=%s serial=%s firmware=%s connection=%s",
            info.get_name(), info.get_serial_number(),
            info.get_firmware_version(), info.get_connection_type(),
        )
        self.pipeline = ob.Pipeline(self.device)
        self.config = ob.Config()
        color_profile = None
        if "color" in self.stream_types:
            color_profile = self._select_profile("color")
            self.config.enable_stream(color_profile)
            self._configure_color()
        if "depth" in self.stream_types:
            depth_profile = (
                self._select_hw_d2c_profile(color_profile)
                if self.align_mode == "HW_D2C"
                else self._select_profile("depth")
            )
            self.config.enable_stream(depth_profile)
            self._configure_depth()
        align_modes = {
            "DISABLE": ob.OBAlignMode.DISABLE,
            "HW_D2C": ob.OBAlignMode.HW_MODE,
            "SW_D2C": ob.OBAlignMode.SW_MODE,
        }
        self.config.set_align_mode(align_modes[self.align_mode])

    def _profile_options(self, stream_type: str):
        options = self.stream_config[stream_type]
        width, height, fps = (
            options["width"], options["height"], options["fps"]
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in (width, height, fps)
        ):
            raise ValueError(f"{stream_type} 的 width/height/fps 必须为正整数")
        format_name = options["format"].upper()
        if stream_type == "depth" and format_name != "Y16":
            raise ValueError("当前深度传输仅支持 Y16 格式")
        if stream_type == "color" and format_name not in (
            "MJPG", "RGB", "BGR", "YUYV"
        ):
            raise ValueError(f"当前不支持的 color 格式: {format_name}")
        return width, height, fps, format_name

    def _select_profile(self, stream_type: str):
        width, height, fps, format_name = self._profile_options(stream_type)
        sensor_type = (
            ob.OBSensorType.COLOR_SENSOR
            if stream_type == "color" else ob.OBSensorType.DEPTH_SENSOR
        )
        profiles = self.pipeline.get_stream_profile_list(sensor_type)
        try:
            profile = profiles.get_video_stream_profile(
                width, height, getattr(ob.OBFormat, format_name), fps
            )
        except Exception as exc:
            raise ValueError(
                f"相机不支持 {stream_type} 配置 "
                f"{width}x{height} {format_name} {fps} FPS"
            ) from exc
        logger.info(
            "Selected %s profile: %sx%s %s %s FPS",
            stream_type, width, height, format_name, fps,
        )
        return profile

    def _select_hw_d2c_profile(self, color_profile):
        width, height, fps, format_name = self._profile_options("depth")
        profiles = self.pipeline.get_d2c_depth_profile_list(
            color_profile, ob.OBAlignMode.HW_MODE
        )
        for index in range(profiles.get_count()):
            profile = profiles.get_stream_profile_by_index(
                index
            ).as_video_stream_profile()
            if (
                profile.get_width() == width
                and profile.get_height() == height
                and profile.get_fps() == fps
                and profile.get_format() == getattr(ob.OBFormat, format_name)
            ):
                return profile
        raise ValueError(
            f"相机没有与当前 color 配置兼容的 HW_D2C depth profile: "
            f"{width}x{height} {format_name} {fps} FPS"
        )

    def _set_bool_property(self, name: str, value: Optional[bool]) -> None:
        if value is None:
            return
        if not isinstance(value, bool):
            raise ValueError(f"{name} 必须为 true、false 或 null")
        property_id = getattr(ob.OBPropertyID, name)
        if not self.device.is_property_supported(
            property_id, ob.OBPermissionType.PERMISSION_WRITE
        ):
            raise RuntimeError(f"设备不支持写入 {name}")
        self.device.set_bool_property(property_id, value)
        logger.info("Set %s=%s", name, value)

    def _set_int_property(self, name: str, value: Optional[int]) -> None:
        if value is None:
            return
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} 必须为整数或 null")
        property_id = getattr(ob.OBPropertyID, name)
        if not self.device.is_property_supported(
            property_id, ob.OBPermissionType.PERMISSION_WRITE
        ):
            raise RuntimeError(f"设备不支持写入 {name}")
        value_range = self.device.get_int_property_range(property_id)
        if not value_range.min <= value <= value_range.max:
            raise ValueError(
                f"{name}={value} 超出设备范围 "
                f"[{value_range.min}, {value_range.max}]"
            )
        if (
            value_range.step > 0
            and (value - value_range.min) % value_range.step != 0
        ):
            raise ValueError(f"{name}={value} 不符合步长 {value_range.step}")
        self.device.set_int_property(property_id, value)
        logger.info("Set %s=%s", name, value)

    def _configure_color(self) -> None:
        options = self.stream_config["color"]
        if options["auto_exposure"] is not False and (
            options["exposure"] is not None or options["gain"] is not None
        ):
            raise ValueError("设置 color.exposure/gain 前必须关闭自动曝光")
        self._set_bool_property(
            "OB_PROP_COLOR_AUTO_EXPOSURE_BOOL", options["auto_exposure"]
        )
        self._set_int_property(
            "OB_PROP_COLOR_EXPOSURE_INT", options["exposure"]
        )
        self._set_int_property("OB_PROP_COLOR_GAIN_INT", options["gain"])
        if (
            options["auto_white_balance"] is not False
            and options["white_balance"] is not None
        ):
            raise ValueError("设置 color.white_balance 前必须关闭自动白平衡")
        self._set_bool_property(
            "OB_PROP_COLOR_AUTO_WHITE_BALANCE_BOOL",
            options["auto_white_balance"],
        )
        self._set_int_property(
            "OB_PROP_COLOR_WHITE_BALANCE_INT", options["white_balance"]
        )
        for key, property_name in (
            ("brightness", "OB_PROP_COLOR_BRIGHTNESS_INT"),
            ("sharpness", "OB_PROP_COLOR_SHARPNESS_INT"),
            ("saturation", "OB_PROP_COLOR_SATURATION_INT"),
            ("contrast", "OB_PROP_COLOR_CONTRAST_INT"),
        ):
            self._set_int_property(property_name, options[key])
        frequency = options["power_line_frequency"]
        if frequency not in (None, 0, 50, 60):
            raise ValueError("color.power_line_frequency 只能为 0、50、60 或 null")
        if frequency is not None:
            self._set_int_property(
                "OB_PROP_COLOR_POWER_LINE_FREQUENCY_INT",
                {0: 0, 50: 1, 60: 2}[frequency],
            )

    def _configure_depth(self) -> None:
        options = self.stream_config["depth"]
        if options["auto_exposure"] is not False and (
            options["exposure"] is not None or options["gain"] is not None
        ):
            raise ValueError("设置 depth.exposure/gain 前必须关闭自动曝光")
        self._set_bool_property(
            "OB_PROP_DEPTH_AUTO_EXPOSURE_BOOL", options["auto_exposure"]
        )
        self._set_int_property(
            "OB_PROP_DEPTH_EXPOSURE_INT", options["exposure"]
        )
        self._set_int_property("OB_PROP_DEPTH_GAIN_INT", options["gain"])

    def start(self) -> None:
        if self._running.is_set():
            return
        self.pipeline.start(self.config)
        self._pipeline_started = True
        self._capture_error = None
        self._running.set()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name=f"OrbbecCamera-{self.device_sn}",
            daemon=True,
        )
        self._thread.start()
        logger.info("Orbbec camera started: serial=%s", self.device_sn)

    def stop(self) -> None:
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                logger.warning(
                    "Capture worker did not stop within 2 seconds: serial=%s",
                    self.device_sn,
                )
            self._thread = None
        if self._pipeline_started:
            try:
                self.pipeline.stop()
            finally:
                self._pipeline_started = False
        with self._frame_lock:
            self._packets = {name: None for name in self.stream_types}
        logger.info("Orbbec camera stopped: serial=%s", self.device_sn)

    def _capture_loop(self) -> None:
        while self._running.is_set():
            try:
                frames = self.pipeline.wait_for_frames(1000)
                if frames is None:
                    continue
                timestamp = time.time()
                if "color" in self.stream_types:
                    color_frame = frames.get_color_frame()
                    if color_frame is not None:
                        self._save_packet(
                            "color", timestamp,
                            self._color_frame_to_bgr(color_frame),
                        )
                if "depth" in self.stream_types:
                    depth_frame = frames.get_depth_frame()
                    if depth_frame is not None:
                        self._save_packet(
                            "depth", timestamp,
                            self._depth_frame_to_uint16(depth_frame),
                            depth_frame.get_depth_scale(),
                        )
            except Exception as exc:
                if self._running.is_set():
                    self._capture_error = exc
                    logger.exception(
                        "Orbbec capture failed; retrying: serial=%s", self.device_sn
                    )
                    time.sleep(0.1)

    def _save_packet(
        self,
        stream_type: str,
        timestamp: float,
        frame: np.ndarray,
        depth_scale: Optional[float] = None,
    ) -> None:
        with self._frame_lock:
            self._frame_ids[stream_type] += 1
            self._packets[stream_type] = FramePacket(
                self._frame_ids[stream_type], timestamp, frame, depth_scale
            )
            self._capture_error = None

    def get_frame(self) -> Optional[np.ndarray]:
        packet = self.get_frame_packet("color")
        return None if packet is None else packet.frame

    def get_frame_packet(
        self, stream_type: str = "color"
    ) -> Optional[FramePacket]:
        with self._frame_lock:
            return self._packets.get(stream_type)

    @property
    def frame_id(self) -> int:
        with self._frame_lock:
            return self._frame_ids.get("color", 0)

    @property
    def timestamp(self) -> Optional[float]:
        packet = self.get_frame_packet("color")
        return None if packet is None else packet.timestamp

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    @property
    def capture_error(self) -> Optional[BaseException]:
        return self._capture_error

    @staticmethod
    def _color_frame_to_bgr(frame) -> np.ndarray:
        width = frame.get_width()
        height = frame.get_height()
        fmt = frame.get_format()
        data = np.frombuffer(frame.get_data(), dtype=np.uint8)
        if fmt == ob.OBFormat.MJPG:
            image = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if image is None:
                raise RuntimeError("MJPG 彩色帧解码失败")
            return image
        if fmt == ob.OBFormat.RGB:
            return cv2.cvtColor(
                data.reshape((height, width, 3)), cv2.COLOR_RGB2BGR
            )
        if fmt == ob.OBFormat.BGR:
            return data.reshape((height, width, 3)).copy()
        if fmt == ob.OBFormat.YUYV:
            return cv2.cvtColor(
                data.reshape((height, width, 2)), cv2.COLOR_YUV2BGR_YUY2
            )
        raise RuntimeError(f"暂不支持的彩色图格式: {fmt}")

    @staticmethod
    def _depth_frame_to_uint16(frame) -> np.ndarray:
        if frame.get_format() != ob.OBFormat.Y16:
            raise RuntimeError(f"暂不支持的深度图格式: {frame.get_format()}")
        return np.frombuffer(
            frame.get_data(), dtype=np.uint16
        ).reshape((frame.get_height(), frame.get_width())).copy()
