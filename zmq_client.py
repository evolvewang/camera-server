"""ZeroMQ subscriber API and a small local preview example."""

import json
from typing import Any, Optional

import cv2
import numpy as np
import zmq

from core.logger import get_logger
from service.frame_codec import decode_frame


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

        if topic_text != self.device_sn:
            raise ValueError(f"收到非目标相机 topic: {topic_text}")
        frame = decode_frame(metadata, image_bytes, topic_text)

        self.stream_shapes[metadata["stream_type"]] = tuple(
            int(size) for size in frame.shape
        )
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
    device_sn = "AY68B520047"
    endpoint = "tcp://10.20.2.171:5559"
    receive_timeout_ms = 5000
    receive_hwm = 2
    show = True
    raise SystemExit(
        main(device_sn, endpoint, receive_timeout_ms, receive_hwm, show)
    )
