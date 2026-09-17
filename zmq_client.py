"""Client API and runnable example for the camera RGB stream."""

import json
from typing import Optional

import cv2
import numpy as np
import zmq

from core.config import app_config
from core.logger import get_logger
from service.video_protocol import JPEG_ENCODING, PROTOCOL_VERSION


logger = get_logger(__name__)


class ZmqVideoSubscriber:
    """Receive JPEG frames and decode them to OpenCV BGR arrays."""

    def __init__(
        self,
        device_sn: str,
        endpoint: str,
        receive_timeout_ms: Optional[int],
        receive_hwm: int,
        context: Optional[zmq.Context] = None,
    ):
        if not isinstance(device_sn, str) or not device_sn.strip():
            raise ValueError("device_sn 不能为空")
        if not isinstance(endpoint, str) or not endpoint:
            raise ValueError("endpoint 不能为空")
        if (
            receive_timeout_ms is not None
            and (
                isinstance(receive_timeout_ms, bool)
                or not isinstance(receive_timeout_ms, int)
                or receive_timeout_ms <= 0
            )
        ):
            raise ValueError("receive_timeout_ms 必须是正整数或 None")
        if (
            isinstance(receive_hwm, bool)
            or not isinstance(receive_hwm, int)
            or receive_hwm <= 0
        ):
            raise ValueError("receive_hwm 必须是正整数")

        self.endpoint = endpoint
        self.device_sn = device_sn.strip()
        self._context = context or zmq.Context()
        self._owns_context = context is None
        self._closed = False
        self.socket = self._context.socket(zmq.SUB)
        self.socket.setsockopt(zmq.RCVHWM, receive_hwm)
        self.socket.setsockopt(zmq.LINGER, 0)
        if receive_timeout_ms is not None:
            self.socket.setsockopt(zmq.RCVTIMEO, receive_timeout_ms)
        self.socket.setsockopt_string(zmq.SUBSCRIBE, self.device_sn)
        self.socket.connect(self.endpoint)
        logger.info(
            "Connected to ZMQ video stream: endpoint=%s device_sn=%s",
            self.endpoint,
            self.device_sn,
        )

    def receive(self):
        """Return ``(metadata, BGR frame)`` or raise ``TimeoutError``."""
        try:
            parts = self.socket.recv_multipart()
        except zmq.Again as exc:
            raise TimeoutError(
                f"等待相机 {self.device_sn} 视频帧超时: {self.endpoint}"
            ) from exc

        if len(parts) != 3:
            raise ValueError(f"无效视频消息：期望 3 段，实际 {len(parts)} 段")
        topic, metadata_bytes, image_bytes = parts
        topic_text = topic.decode("utf-8")
        if topic_text != self.device_sn:
            raise ValueError(f"收到非目标相机 topic: {topic_text}")

        metadata = json.loads(metadata_bytes.decode("utf-8"))
        if metadata.get("protocol_version") != PROTOCOL_VERSION:
            raise ValueError(
                f"不支持的协议版本: {metadata.get('protocol_version')}"
            )
        if metadata.get("device_sn") != topic_text:
            raise ValueError("消息 topic 与 metadata.device_sn 不一致")
        if metadata.get("encoding") != JPEG_ENCODING:
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


def main() -> int:
    if not app_config.camera.device_sn:
        logger.error("configs/config.yaml 中的 camera.device_sn 不能为空")
        return 1

    try:
        with ZmqVideoSubscriber(
            device_sn=app_config.camera.device_sn,
            endpoint=app_config.zmq.subscriber_endpoint,
            receive_timeout_ms=app_config.zmq.receive_timeout_ms,
            receive_hwm=app_config.zmq.receive_hwm,
        ) as subscriber:
            while True:
                metadata, frame = subscriber.receive()
                logger.debug(
                    f"frame_id={metadata['frame_id']} "
                    f"timestamp={metadata['timestamp']:.6f} shape={frame.shape}"
                )
                if app_config.client.show:
                    cv2.imshow(app_config.camera.device_sn, frame)
                    if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                        break
    except KeyboardInterrupt:
        pass
    except TimeoutError as exc:
        logger.error("%s", exc)
        return 1
    finally:
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    main()
