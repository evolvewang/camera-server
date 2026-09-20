"""Root entry point for the Orbbec color/depth ZeroMQ service."""

import signal
import threading
from typing import Optional

from camera.orbbec_camera import OrbbecCamera, discover_device_serial_numbers
from core.config import app_config
from core.logger import get_logger
from service.zmq_publisher import ZmqVideoPublisher
from service.zmq_snapshot import ZmqSnapshotResponder


logger = get_logger(__name__)


def resolve_device_sn(requested_sn: Optional[str]) -> str:
    if requested_sn:
        return requested_sn

    serial_numbers = discover_device_serial_numbers()
    if len(serial_numbers) == 1:
        logger.info(
            "Automatically selected the only Orbbec device: %s",
            serial_numbers[0],
        )
        return serial_numbers[0]
    if not serial_numbers:
        raise RuntimeError("没有发现 Orbbec 相机")
    raise RuntimeError(
        f"发现多台 Orbbec 相机 {serial_numbers}，"
        "请在 configs/config.yaml 中配置 camera.device_sn"
    )


class ZmqVideoServer:
    def __init__(self):
        self.camera = None
        self.publisher = None
        self.snapshot = None
        self.stop_requested = threading.Event()

    def start(self) -> int:
        self._register_signal_handlers()

        try:
            device_sn = resolve_device_sn(app_config["camera"]["device_sn"])
            self.camera = OrbbecCamera(
                device_sn=device_sn,
                stream_config=app_config["stream"],
            )
            self.publisher = ZmqVideoPublisher(
                camera=self.camera,
                endpoint=app_config["zmq"]["publisher_endpoint"],
                jpeg_quality=app_config["stream"]["jpeg_quality"],
                send_hwm=app_config["zmq"]["send_hwm"],
            )
            self.snapshot = ZmqSnapshotResponder(
                camera=self.camera,
                endpoint=app_config["zmq"]["snapshot_endpoint"],
                jpeg_quality=app_config["stream"]["jpeg_quality"],
                max_frame_age_ms=app_config["zmq"]["snapshot_max_frame_age_ms"],
            )

            self.camera.start()
            self.publisher.start()
            self.snapshot.start()
            logger.info(
                "Camera Server ready: device_sn=%s streams=%s pub=%s snapshot=%s",
                device_sn,
                self.camera.stream_types,
                app_config["zmq"]["publisher_endpoint"],
                app_config["zmq"]["snapshot_endpoint"],
            )

            while not self.stop_requested.wait(0.5):
                if self.publisher.error is not None:
                    raise RuntimeError("ZMQ 发布线程异常退出") from self.publisher.error
                if self.snapshot.error is not None:
                    raise RuntimeError("ZMQ 拍照线程异常退出") from self.snapshot.error
        except KeyboardInterrupt:
            logger.info("Camera Server interrupted by user")
            return 0
        except Exception:
            logger.exception("Camera Server stopped because of an error")
            return 1
        finally:
            self.stop()
        return 0

    def stop(self) -> None:
        self.stop_requested.set()
        if self.snapshot is not None:
            self.snapshot.stop()
        if self.publisher is not None:
            self.publisher.stop()
        if self.camera is not None:
            self.camera.stop()
        logger.info("Camera Server stopped")

    def _register_signal_handlers(self) -> None:
        def request_stop(signum: int, frame) -> None:
            logger.info("Stop requested by signal %s", signum)
            self.stop_requested.set()

        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, request_stop)
        if hasattr(signal, "SIGINT"):
            signal.signal(signal.SIGINT, request_stop)


if __name__ == "__main__":
    server = ZmqVideoServer()
    raise SystemExit(server.start())
