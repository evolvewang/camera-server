"""ZeroMQ subscriber API and a small local preview example."""

import json
import math
from typing import Any, Optional

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
    """Receive color frames as BGR uint8 and depth frames as raw uint16."""

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
        self._context = context if context is not None else zmq.Context()
        self._owns_context = context is None
        self._closed = False
        self.stream_shapes: dict[str, tuple[int, ...]] = {}
        self.socket = None

        try:
            self.socket = self._context.socket(zmq.SUB)
            self.socket.setsockopt(zmq.RCVHWM, receive_hwm)
            self.socket.setsockopt(zmq.LINGER, 0)
            if receive_timeout_ms is not None:
                self.socket.setsockopt(zmq.RCVTIMEO, receive_timeout_ms)
            self.socket.setsockopt_string(zmq.SUBSCRIBE, self.device_sn)
            self.socket.connect(self.endpoint)
        except Exception:
            self.close()
            raise

        logger.info(
            "Connected to ZMQ video stream: endpoint=%s device_sn=%s",
            self.endpoint,
            self.device_sn,
        )

    def receive(self) -> tuple[dict[str, Any], np.ndarray]:
        """Return one ``(metadata, frame)`` pair or raise ``TimeoutError``."""
        if self._closed:
            raise RuntimeError("视频订阅器已经关闭")

        try:
            parts = self.socket.recv_multipart()
        except zmq.Again as exc:
            raise TimeoutError(
                f"等待相机 {self.device_sn} 视频帧超时: {self.endpoint}"
            ) from exc

        if len(parts) != 3:
            raise ValueError(f"无效视频消息：期望 3 段，实际 {len(parts)} 段")
        topic, metadata_bytes, image_bytes = parts
        try:
            topic_text = topic.decode("utf-8")
            metadata = json.loads(metadata_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("视频消息的 topic 或 metadata 不是有效 UTF-8/JSON") from exc

        self._validate_metadata(metadata, topic_text)
        stream_type = metadata["stream_type"]

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
            frame.shape[:2] != (metadata["height"], metadata["width"])
            or metadata["channels"] != expected_channels
        ):
            raise ValueError("图像尺寸或通道数与元数据不一致")
        if stream_type == "color" and (
            frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3
        ):
            raise ValueError("彩色图必须为三通道 uint8 BGR")
        if stream_type == "depth" and (
            frame.dtype != np.uint16 or frame.ndim != 2
        ):
            raise ValueError("深度图必须为单通道 uint16")

        self.stream_shapes[stream_type] = tuple(int(size) for size in frame.shape)
        return metadata, frame

    def close(self) -> None:
        """Close the subscriber socket and an internally created context."""
        if self._closed:
            return
        self._closed = True
        if self.socket is not None:
            self.socket.close(linger=0)
            self.socket = None
        if self._owns_context:
            self._context.term()

    def __enter__(self) -> "ZmqVideoSubscriber":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _validate_metadata(self, metadata: object, topic_text: str) -> None:
        if not isinstance(metadata, dict):
            raise ValueError("视频 metadata 必须是 JSON 对象")
        if topic_text != self.device_sn:
            raise ValueError(f"收到非目标相机 topic: {topic_text}")
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
            raise ValueError(f"不支持的视频流或编码: {stream_type}/{encoding}")

        for field in ("width", "height", "channels"):
            value = metadata.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"metadata.{field} 必须是正整数")
        frame_id = metadata.get("frame_id")
        if isinstance(frame_id, bool) or not isinstance(frame_id, int) or frame_id < 0:
            raise ValueError("metadata.frame_id 必须是非负整数")
        timestamp = metadata.get("timestamp")
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or not math.isfinite(timestamp)
        ):
            raise ValueError("metadata.timestamp 必须是有限数字")

        if stream_type == "depth":
            depth_scale = metadata.get("depth_scale")
            if (
                isinstance(depth_scale, bool)
                or not isinstance(depth_scale, (int, float))
                or not math.isfinite(depth_scale)
                or depth_scale <= 0
            ):
                raise ValueError("无效 depth_scale")


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
                    "stream=%s frame_id=%s timestamp=%.6f shape=%s",
                    metadata["stream_type"],
                    metadata["frame_id"],
                    metadata["timestamp"],
                    frame.shape,
                )
                if show:
                    if metadata["stream_type"] == "depth":
                        depth_mm = (
                            frame.astype(np.float32) * metadata["depth_scale"]
                        )
                        preview = cv2.convertScaleAbs(
                            depth_mm, alpha=255.0 / 4000
                        )
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
    device_sn = ""
    endpoint = "tcp://127.0.0.1:5558"
    receive_timeout_ms = 5000
    receive_hwm = 2
    show = True
    raise SystemExit(
        main(device_sn, endpoint, receive_timeout_ms, receive_hwm, show)
    )
