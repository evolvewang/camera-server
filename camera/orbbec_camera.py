"""Orbbec RGB camera adapter.

The rest of the application only sees OpenCV BGR ``ndarray`` frames. Orbbec
SDK objects and pixel-format conversion stay in this module.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional, Tuple

import cv2
import numpy as np
import pyorbbecsdk as ob


LOGGER = logging.getLogger(__name__)
FramePacket = Tuple[int, float, np.ndarray]


def discover_device_serial_numbers() -> list[str]:
    """Return serial numbers for the currently connected Orbbec devices."""
    context = ob.Context()
    device_list = context.query_devices()
    return [
        device_list.get_device_serial_number_by_index(index)
        for index in range(device_list.get_count())
    ]


class OrbbecCamera:
    """Continuously capture the newest Orbbec color frame in a worker thread.

    ``get_frame()`` and ``get_frame_packet()`` return a shared ndarray reference.
    Callers may read it but must not modify it in place.
    """

    def __init__(self, device_sn: str):
        self.device_sn = device_sn

        self.context = None
        self.device = None
        self.pipeline = None
        self.config = None

        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pipeline_started = False

        self._frame: Optional[np.ndarray] = None
        self._frame_lock = threading.Lock()
        self._frame_id = 0
        self._timestamp: Optional[float] = None
        self._capture_error: Optional[BaseException] = None

        self._init_camera()

    def _init_camera(self) -> None:
        self.context = ob.Context()
        device_list = self.context.query_devices()
        available_sn = [
            device_list.get_device_serial_number_by_index(index)
            for index in range(device_list.get_count())
        ]

        if self.device_sn not in available_sn:
            raise RuntimeError(
                f"没有找到相机 {self.device_sn}，当前设备: {available_sn}"
            )

        self.device = device_list.get_device_by_serial_number(self.device_sn)
        device_info = self.device.get_device_info()
        LOGGER.info(
            "Orbbec device selected: name=%s serial=%s firmware=%s connection=%s",
            device_info.get_name(),
            device_info.get_serial_number(),
            device_info.get_firmware_version(),
            device_info.get_connection_type(),
        )

        self.pipeline = ob.Pipeline(self.device)
        self.config = ob.Config()
        color_profiles = self.pipeline.get_stream_profile_list(
            ob.OBSensorType.COLOR_SENSOR
        )
        color_profile = color_profiles.get_default_video_stream_profile()
        LOGGER.info(
            "Default color stream: %sx%s@%s format=%s",
            color_profile.get_width(),
            color_profile.get_height(),
            color_profile.get_fps(),
            color_profile.get_format(),
        )
        self.config.enable_stream(color_profile)

    def start(self) -> None:
        """Start the pipeline and capture worker; repeated calls are harmless."""
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
        LOGGER.info("Orbbec camera started: serial=%s", self.device_sn)

    def stop(self) -> None:
        """Stop capture and release the pipeline; repeated calls are harmless."""
        self._running.clear()

        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                LOGGER.warning(
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
            self._frame = None
            self._timestamp = None

        LOGGER.info("Orbbec camera stopped: serial=%s", self.device_sn)

    def _capture_loop(self) -> None:
        while self._running.is_set():
            try:
                frames = self.pipeline.wait_for_frames(1000)
                if frames is None:
                    continue

                color_frame = frames.get_color_frame()
                if color_frame is None:
                    continue

                image = self._color_frame_to_bgr(color_frame)
                if image is None:
                    continue

                timestamp = time.time()
                with self._frame_lock:
                    self._frame = image
                    self._frame_id += 1
                    self._timestamp = timestamp
                    self._capture_error = None
            except Exception as exc:  # SDK errors must not silently kill capture.
                if self._running.is_set():
                    self._capture_error = exc
                    LOGGER.exception(
                        "Orbbec capture failed; retrying: serial=%s", self.device_sn
                    )
                    time.sleep(0.1)

    def get_frame(self) -> Optional[np.ndarray]:
        """Return the newest BGR frame, or ``None`` before the first frame."""
        with self._frame_lock:
            return self._frame

    def get_frame_packet(self) -> Optional[FramePacket]:
        """Atomically return ``(frame_id, timestamp, BGR frame)`` for publishing."""
        with self._frame_lock:
            if self._frame is None or self._timestamp is None:
                return None
            return self._frame_id, self._timestamp, self._frame

    @property
    def frame_id(self) -> int:
        with self._frame_lock:
            return self._frame_id

    @property
    def timestamp(self) -> Optional[float]:
        with self._frame_lock:
            return self._timestamp

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
            image = data.reshape((height, width, 3))
            return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        if fmt == ob.OBFormat.BGR:
            return data.reshape((height, width, 3))
        if fmt == ob.OBFormat.YUYV:
            image = data.reshape((height, width, 2))
            return cv2.cvtColor(image, cv2.COLOR_YUV2BGR_YUY2)
        raise RuntimeError(f"暂不支持的彩色图格式: {fmt}")
