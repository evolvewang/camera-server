"""Executable service that publishes one Orbbec RGB stream over ZeroMQ."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import threading

from camera.orbbec_camera import OrbbecCamera, discover_device_serial_numbers
from service.zmq_publisher import ZmqVideoPublisher


LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="发布 Orbbec RGB 相机实时流")
    parser.add_argument(
        "--device-sn",
        default=os.environ.get("CAMERA_DEVICE_SN"),
        help="Orbbec 序列号；仅连接一台设备时可省略",
    )
    parser.add_argument(
        "--camera-id",
        default=os.environ.get("CAMERA_ID", "CAM_01"),
        help="对外发布的逻辑相机 ID/topic",
    )
    parser.add_argument(
        "--bind",
        default=os.environ.get("CAMERA_ZMQ_BIND", "tcp://0.0.0.0:5558"),
        help="ZMQ PUB 绑定端点",
    )
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=int(os.environ.get("CAMERA_JPEG_QUALITY", "85")),
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def resolve_device_sn(requested_sn: str | None) -> str:
    if requested_sn:
        return requested_sn
    serial_numbers = discover_device_serial_numbers()
    if len(serial_numbers) == 1:
        LOGGER.info(
            "Automatically selected the only Orbbec device: %s", serial_numbers[0]
        )
        return serial_numbers[0]
    if not serial_numbers:
        raise RuntimeError("没有发现 Orbbec 相机")
    raise RuntimeError(
        f"发现多台 Orbbec 相机 {serial_numbers}，请使用 --device-sn 指定设备"
    )


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    camera = None
    publisher = None
    stop_requested = threading.Event()

    def request_stop(signum, frame) -> None:
        LOGGER.info("Stop requested by signal %s", signum)
        stop_requested.set()

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, request_stop)

    try:
        device_sn = resolve_device_sn(args.device_sn)
        camera = OrbbecCamera(device_sn=device_sn)
        publisher = ZmqVideoPublisher(
            camera=camera,
            camera_id=args.camera_id,
            endpoint=args.bind,
            jpeg_quality=args.jpeg_quality,
        )
        camera.start()
        publisher.start()
        LOGGER.info(
            "Camera Server ready: serial=%s camera=%s bind=%s",
            device_sn,
            args.camera_id,
            args.bind,
        )

        while not stop_requested.wait(0.5):
            if publisher.error is not None:
                raise RuntimeError("ZMQ 发布线程异常退出") from publisher.error
    except KeyboardInterrupt:
        pass
    except Exception:
        LOGGER.exception("Camera Server stopped because of an error")
        return 1
    finally:
        if publisher is not None:
            publisher.stop()
        if camera is not None:
            camera.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
