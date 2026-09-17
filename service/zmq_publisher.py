import cv2
import json
import time
import threading
import zmq

import numpy as np
import pyorbbecsdk as ob


class ZmqVideoPublisher:

    def __init__(
        self,
        camera,
        camera_id="CAM_01",
        endpoint="tcp://0.0.0.0:5558",
        jpeg_quality=85,
    ):
        self.camera = camera
        self.camera_id = camera_id

        self.endpoint = endpoint
        self.jpeg_quality = jpeg_quality

        self._running = False
        self._thread = None

    def start(self):

        if self._running:
            return

        self._running = True

        self._thread = threading.Thread(
            target=self._publish_loop,
            daemon=True,
            name="ZmqVideoPublisher",
        )

        self._thread.start()

        print(
            f"ZMQ video stream started: "
            f"{self.endpoint}"
        )

    def stop(self):

        self._running = False

        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _publish_loop(self):

        context = zmq.Context()

        socket = context.socket(zmq.PUB)

        # 实时视频不希望积压大量旧帧
        socket.setsockopt(zmq.SNDHWM, 2)
        socket.setsockopt(zmq.LINGER, 0)

        socket.bind(self.endpoint)

        last_frame_id = -1

        try:

            while self._running:

                packet = self.camera.get_frame_packet()

                if packet is None:
                    time.sleep(0.005)
                    continue

                frame_id, timestamp, frame = packet

                # 防止同一帧重复发送
                if frame_id == last_frame_id:
                    time.sleep(0.001)
                    continue

                last_frame_id = frame_id

                success, encoded = cv2.imencode(
                    ".jpg",
                    frame,
                    [
                        cv2.IMWRITE_JPEG_QUALITY,
                        self.jpeg_quality,
                    ],
                )

                if not success:
                    continue

                metadata = {
                    "camera_id": self.camera_id,
                    "frame_id": frame_id,
                    "timestamp": timestamp,
                    "width": frame.shape[1],
                    "height": frame.shape[0],
                    "channels": frame.shape[2],
                    "encoding": "jpeg",
                }

                socket.send_multipart([
                    self.camera_id.encode("utf-8"),
                    json.dumps(metadata).encode("utf-8"),
                    encoded.tobytes(),
                ])

        finally:

            socket.close()
            context.term()

            print("ZMQ video stream stopped.")


class OrbbecCamera:

    def __init__(self, device_sn: str):
        self.device_sn = device_sn

        self.context = None
        self.device = None
        self.pipeline = None
        self.config = None

        self._running = False
        self._thread = None

        self._frame = None
        self._frame_id = 0
        self._timestamp = None

        self._frame_lock = threading.Lock()

        self._init_camera()

    def _init_camera(self):
        self.context = ob.Context()
        device_list = self.context.query_devices()

        available_sn = [
            device_list.get_device_serial_number_by_index(i)
            for i in range(device_list.get_count())
        ]

        if self.device_sn not in available_sn:
            raise RuntimeError(
                f"没有找到相机 {self.device_sn}，"
                f"当前设备: {available_sn}"
            )

        self.device = (
            device_list.get_device_by_serial_number(
                self.device_sn
            )
        )

        info = self.device.get_device_info()

        print("名称:", info.get_name())
        print("序列号:", info.get_serial_number())
        print("固件版本:", info.get_firmware_version())
        print("连接类型:", info.get_connection_type())

        self.pipeline = ob.Pipeline(self.device)
        self.config = ob.Config()

        color_profiles = (
            self.pipeline.get_stream_profile_list(
                ob.OBSensorType.COLOR_SENSOR
            )
        )

        color_profile = (
            color_profiles.get_default_video_stream_profile()
        )

        print(
            "Color Stream:",
            f"{color_profile.get_width()}x"
            f"{color_profile.get_height()}",
            f"@{color_profile.get_fps()}FPS",
            f"format={color_profile.get_format()}",
        )

        self.config.enable_stream(color_profile)

    # ---------------------------------------------------------
    # lifecycle
    # ---------------------------------------------------------

    def start(self):
        if self._running:
            return

        self.pipeline.start(self.config)

        self._running = True

        self._thread = threading.Thread(
            target=self._capture_loop,
            daemon=True,
            name=f"Orbbec-{self.device_sn}",
        )

        self._thread.start()

        print(f"Camera {self.device_sn} started.")

    def stop(self):
        if not self._running:
            return

        self._running = False

        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

        self.pipeline.stop()

        print(f"Camera {self.device_sn} stopped.")

    # ---------------------------------------------------------
    # capture
    # ---------------------------------------------------------

    def _capture_loop(self):

        while self._running:

            try:
                frames = self.pipeline.wait_for_frames(1000)

                if frames is None:
                    continue

                color_frame = frames.get_color_frame()

                if color_frame is None:
                    continue

                image = self._color_frame_to_bgr(
                    color_frame
                )

                if image is None:
                    continue

                timestamp = time.time()

                with self._frame_lock:
                    self._frame = image
                    self._frame_id += 1
                    self._timestamp = timestamp

            except Exception as e:
                if self._running:
                    print(f"Camera capture error: {e}")

    # ---------------------------------------------------------
    # public interface
    # ---------------------------------------------------------

    def get_frame(self):
        """
        外部最简单的接口。

        Returns:
            np.ndarray | None
        """
        with self._frame_lock:
            return self._frame

    def get_frame_packet(self):
        """
        给 ZMQ 服务使用。

        Returns:
            (frame_id, timestamp, frame) | None
        """
        with self._frame_lock:

            if self._frame is None:
                return None

            return (
                self._frame_id,
                self._timestamp,
                self._frame,
            )

    @property
    def is_running(self):
        return self._running

    # ---------------------------------------------------------
    # convert
    # ---------------------------------------------------------

    @staticmethod
    def _color_frame_to_bgr(frame):

        width = frame.get_width()
        height = frame.get_height()
        fmt = frame.get_format()

        data = np.frombuffer(
            frame.get_data(),
            dtype=np.uint8,
        )

        if fmt == ob.OBFormat.MJPG:
            return cv2.imdecode(
                data,
                cv2.IMREAD_COLOR,
            )

        if fmt == ob.OBFormat.RGB:

            image = data.reshape(
                (height, width, 3)
            )

            return cv2.cvtColor(
                image,
                cv2.COLOR_RGB2BGR,
            )

        if fmt == ob.OBFormat.BGR:
            return data.reshape(
                (height, width, 3)
            )

        if fmt == ob.OBFormat.YUYV:

            image = data.reshape(
                (height, width, 2)
            )

            return cv2.cvtColor(
                image,
                cv2.COLOR_YUV2BGR_YUY2,
            )

        raise RuntimeError(
            f"暂不支持的彩色格式: {fmt}"
        )


if __name__ == "__main__":


    DEVICE_SN = "CPC7B5300098"
    CAMERA_ID = "CAM_01"

    camera = OrbbecCamera(
        device_sn=DEVICE_SN
    )

    publisher = ZmqVideoPublisher(
        camera=camera,
        camera_id=CAMERA_ID,
        endpoint="tcp://0.0.0.0:5558",
        jpeg_quality=85,
    )

    camera.start()
    publisher.start()

    try:

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("Stopping...")

    finally:

        publisher.stop()
        camera.stop()

