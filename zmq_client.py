"""Client API and runnable example for color and depth streams."""

import json
import math
from typing import Optional

import cv2
import numpy as np
import zmq

from core.logger import get_logger
from service.video_protocol import (
    DEPTH_ENCODING,
    JPEG_ENCODING,
    PROTOCOL_VERSION,
)


logger = get_logger(__name__)


class ZmqVideoSubscriber:
    """Receive color as BGR uint8 and depth as raw uint16 arrays."""

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
        """Return ``(metadata, frame)`` or raise ``TimeoutError``."""
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
        stream_type = metadata.get("stream_type")
        encoding = metadata.get("encoding")
        expected_encoding = {
            "color": JPEG_ENCODING,
            "depth": DEPTH_ENCODING,
        }.get(stream_type)
        if encoding != expected_encoding or expected_encoding is None:
            raise ValueError(
                f"不支持的视频流或编码: {stream_type}/{encoding}"
            )
        image_array = np.frombuffer(image_bytes, dtype=np.uint8)
        decode_mode = (
            cv2.IMREAD_COLOR
            if stream_type == "color" else cv2.IMREAD_UNCHANGED
        )
        frame = cv2.imdecode(image_array, decode_mode)
        if frame is None:
            raise ValueError(f"{stream_type} 视频帧解码失败")
        expected_channels = 3 if stream_type == "color" else 1
        if (
            frame.shape[:2] != (metadata.get("height"), metadata.get("width"))
            or metadata.get("channels") != expected_channels
        ):
            raise ValueError("图像尺寸或通道数与元数据不一致")
        if stream_type == "depth":
            if frame.dtype != np.uint16 or frame.ndim != 2:
                raise ValueError("深度图必须为单通道 uint16")
            depth_scale = metadata.get("depth_scale")
            if (
                isinstance(depth_scale, bool)
                or not isinstance(depth_scale, (int, float))
                or not math.isfinite(depth_scale)
                or depth_scale <= 0
            ):
                raise ValueError("无效 depth_scale")
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


def main(
    device_sn: str,
    endpoint: str,
    receive_timeout_ms: Optional[int],
    receive_hwm: int,
    show: bool,
) -> int:
    if not device_sn:
        logger.error("device_sn 不能为空")
        return 1

    try:
        with ZmqVideoSubscriber(
            device_sn=device_sn,
            endpoint=endpoint,
            receive_timeout_ms=receive_timeout_ms,
            receive_hwm=receive_hwm,
        ) as subscriber:
            while True:
                metadata, frame = subscriber.receive()
                logger.debug(
                    f"stream={metadata['stream_type']} frame_id={metadata['frame_id']} "
                    f"timestamp={metadata['timestamp']:.6f} shape={frame.shape}"
                )
                if show:
                    if metadata["stream_type"] == "depth":
                        # Only the preview is colorized; receive() returns raw uint16.
                        depth_mm = frame.astype(np.float32) * metadata["depth_scale"]
                        preview = cv2.convertScaleAbs(depth_mm, alpha=255.0 / 4000)
                        preview = cv2.applyColorMap(preview, cv2.COLORMAP_JET)
                    else:
                        preview = frame
                    cv2.imshow(f"{device_sn}/{metadata['stream_type']}", preview)
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
    device_sn = "AY6V16300W8"
    endpoint = "tcp://10.20.2.49:5558"
    receive_timeout_ms = 5000
    receive_hwm = 2
    show = True
    main(device_sn, endpoint, receive_timeout_ms, receive_hwm, show)
