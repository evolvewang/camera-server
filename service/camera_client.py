"""Client API and command-line example for the camera RGB stream."""

from __future__ import annotations

import argparse
import json
import logging
from typing import Optional

import cv2
import numpy as np
import zmq


LOGGER = logging.getLogger(__name__)


class ZmqVideoSubscriber:
    """Receive JPEG frames and decode them to OpenCV BGR arrays."""

    def __init__(
        self,
        endpoint: str = "tcp://127.0.0.1:5558",
        camera_id: str = "CAM_01",
        receive_timeout_ms: Optional[int] = 5000,
        context: Optional[zmq.Context] = None,
    ):
        self.endpoint = endpoint
        self.camera_id = camera_id
        self._context = context or zmq.Context()
        self._owns_context = context is None
        self._closed = False
        self.socket = self._context.socket(zmq.SUB)
        self.socket.setsockopt(zmq.RCVHWM, 2)
        self.socket.setsockopt(zmq.LINGER, 0)
        if receive_timeout_ms is not None:
            self.socket.setsockopt(zmq.RCVTIMEO, receive_timeout_ms)
        self.socket.setsockopt_string(zmq.SUBSCRIBE, self.camera_id)
        self.socket.connect(self.endpoint)
        LOGGER.info(
            "Connected to ZMQ video stream: endpoint=%s camera=%s",
            self.endpoint,
            self.camera_id,
        )

    def receive(self):
        """Return ``(metadata, BGR frame)`` or raise ``TimeoutError``."""
        try:
            parts = self.socket.recv_multipart()
        except zmq.Again as exc:
            raise TimeoutError(
                f"等待相机 {self.camera_id} 视频帧超时: {self.endpoint}"
            ) from exc

        if len(parts) != 3:
            raise ValueError(f"无效视频消息：期望 3 段，实际 {len(parts)} 段")
        topic, metadata_bytes, image_bytes = parts
        topic_text = topic.decode("utf-8")
        if topic_text != self.camera_id:
            raise ValueError(f"收到非目标相机 topic: {topic_text}")

        metadata = json.loads(metadata_bytes.decode("utf-8"))
        if metadata.get("camera_id") != topic_text:
            raise ValueError("消息 topic 与 metadata.camera_id 不一致")
        if metadata.get("encoding") != "jpeg":
            raise ValueError(f"不支持的图像编码: {metadata.get('encoding')}")

        image_array = np.frombuffer(image_bytes, dtype=np.uint8)
        frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("JPEG 视频帧解码失败")
        return metadata, frame

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.socket.close(linger=0)
        if self._owns_context:
            self._context.term()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="接收 Camera Server RGB 视频流")
    parser.add_argument("--endpoint", default="tcp://127.0.0.1:5558")
    parser.add_argument("--camera-id", default="CAM_01")
    parser.add_argument("--timeout-ms", type=int, default=5000)
    parser.add_argument("--show", action="store_true", help="使用 OpenCV 窗口显示视频")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        with ZmqVideoSubscriber(
            endpoint=args.endpoint,
            camera_id=args.camera_id,
            receive_timeout_ms=args.timeout_ms,
        ) as subscriber:
            while True:
                metadata, frame = subscriber.receive()
                print(
                    f"frame_id={metadata['frame_id']} "
                    f"timestamp={metadata['timestamp']:.6f} shape={frame.shape}"
                )
                if args.show:
                    cv2.imshow(args.camera_id, frame)
                    if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                        break
    except KeyboardInterrupt:
        pass
    except TimeoutError as exc:
        LOGGER.error("%s", exc)
        return 1
    finally:
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
