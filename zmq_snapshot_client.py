"""One-shot ZeroMQ photo requests and a runnable client example."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np
import zmq

from core.logger import get_logger
from service.frame_codec import decode_frame
from service.video_protocol import SNAPSHOT_PROTOCOL_VERSION


logger = get_logger(__name__)


class SnapshotRemoteError(RuntimeError):
    """An error response returned by the camera snapshot service."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


class ZmqSnapshotClient:
    """Request the latest color and/or depth packets from one camera.

    Like any ZeroMQ socket, this client should only be used from one thread.
    """

    def __init__(
        self,
        device_sn: str,
        endpoint: str,
        request_timeout_ms: int,
        context: Optional[zmq.Context] = None,
    ):
        if not isinstance(device_sn, str) or not device_sn.strip():
            raise ValueError("device_sn 不能为空")
        if not isinstance(endpoint, str) or not endpoint:
            raise ValueError("endpoint 不能为空")
        if (
            isinstance(request_timeout_ms, bool)
            or not isinstance(request_timeout_ms, int)
            or request_timeout_ms <= 0
        ):
            raise ValueError("request_timeout_ms 必须是正整数")

        self.device_sn = device_sn.strip()
        self.endpoint = endpoint
        self.request_timeout_ms = request_timeout_ms
        self._context = context if context is not None else zmq.Context()
        self._owns_context = context is None
        self._closed = False
        self.socket = None
        try:
            self._open_socket()
        except Exception:
            self.close()
            raise

    def capture(
        self,
        stream_types: tuple[str, ...] | list[str] = ("color", "depth"),
    ) -> dict[str, tuple[dict[str, Any], np.ndarray]]:
        """Return requested latest frames keyed by stream type.

        Both streams are sampled from the camera's current cache. They are not
        guaranteed to have the same acquisition timestamp.
        """
        if self._closed:
            raise RuntimeError("拍照客户端已经关闭")
        if (
            not isinstance(stream_types, (tuple, list))
            or not stream_types
            or any(
                type(name) is not str or name not in ("color", "depth")
                for name in stream_types
            )
            or len(set(stream_types)) != len(stream_types)
        ):
            raise ValueError("stream_types 必须是非空且不重复的 color/depth 序列")

        request = {
            "protocol_version": SNAPSHOT_PROTOCOL_VERSION,
            "device_sn": self.device_sn,
            "stream_types": list(stream_types),
        }
        try:
            self.socket.send_json(request)
            reply = self.socket.recv_multipart()
        except zmq.Again as exc:
            # A REQ socket cannot send another request after a missed reply.
            self._reset_socket()
            raise TimeoutError(f"等待相机 {self.device_sn} 拍照响应超时") from exc

        if not reply:
            raise ValueError("拍照响应为空")
        try:
            envelope = json.loads(reply[0].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("拍照响应不是有效 JSON") from exc
        if not isinstance(envelope, dict):
            raise ValueError("拍照响应必须是 JSON 对象")
        if type(envelope.get("protocol_version")) is not int or (
            envelope["protocol_version"] != SNAPSHOT_PROTOCOL_VERSION
        ):
            raise ValueError("不支持的拍照协议版本")

        if envelope.get("status") == "error":
            if len(reply) != 1 or not isinstance(envelope.get("error"), dict):
                raise ValueError("无效的拍照错误响应")
            error = envelope["error"]
            if not isinstance(error.get("code"), str) or not isinstance(
                error.get("message"), str
            ):
                raise ValueError("无效的拍照错误信息")
            raise SnapshotRemoteError(error["code"], error["message"])
        if (
            envelope.get("status") != "ok"
            or envelope.get("device_sn") != self.device_sn
        ):
            raise ValueError("无效的拍照响应状态或相机序列号")

        frame_metadata = envelope.get("frames")
        if not isinstance(frame_metadata, list) or len(frame_metadata) != len(
            stream_types
        ):
            raise ValueError("拍照响应的帧数与请求不一致")
        if len(reply) != 1 + len(frame_metadata):
            raise ValueError("拍照响应的图像分段数量不正确")

        result = {}
        for expected_type, metadata, image_bytes in zip(
            stream_types, frame_metadata, reply[1:]
        ):
            if (
                not isinstance(metadata, dict)
                or metadata.get("stream_type") != expected_type
            ):
                raise ValueError("拍照响应的流类型或顺序与请求不一致")
            result[expected_type] = (
                metadata,
                decode_frame(metadata, image_bytes, self.device_sn),
            )
        return result

    def close(self) -> None:
        """Close the REQ socket and an internally created context."""
        if self._closed:
            return
        self._closed = True
        if self.socket is not None:
            self.socket.close(linger=0)
            self.socket = None
        if self._owns_context:
            self._context.term()

    def __enter__(self) -> "ZmqSnapshotClient":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _open_socket(self) -> None:
        socket = self._context.socket(zmq.REQ)
        try:
            socket.setsockopt(zmq.LINGER, 0)
            socket.setsockopt(zmq.SNDTIMEO, self.request_timeout_ms)
            socket.setsockopt(zmq.RCVTIMEO, self.request_timeout_ms)
            socket.connect(self.endpoint)
        except Exception:
            socket.close(linger=0)
            raise
        self.socket = socket

    def _reset_socket(self) -> None:
        self.socket.close(linger=0)
        self.socket = None
        try:
            self._open_socket()
        except Exception:
            self.close()
            raise


def main(
    device_sn: str,
    endpoint: str,
    stream_types: tuple[str, ...],
    request_timeout_ms: int,
    save_dir: Optional[Path],
    show: bool,
) -> int:
    """Take one snapshot; optionally save and preview each returned image."""
    if not device_sn:
        logger.error("请先在入口块填写 device_sn")
        return 1
    try:
        with ZmqSnapshotClient(device_sn, endpoint, request_timeout_ms) as client:
            snapshots = client.capture(stream_types)
        if save_dir is not None:
            save_dir.mkdir(parents=True, exist_ok=True)
            capture_time = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        for stream_type, (metadata, frame) in snapshots.items():
            logger.info(
                "snapshot stream=%s frame_id=%s timestamp=%.6f shape=%s",
                stream_type,
                metadata["frame_id"],
                metadata["timestamp"],
                frame.shape,
            )
            if stream_type == "depth":
                logger.info("depth_scale=%s mm/raw", metadata["depth_scale"])
            if save_dir is not None:
                suffix = ".jpg" if stream_type == "color" else ".png"
                output = save_dir / (
                    f"{device_sn}_{stream_type}_{metadata['frame_id']}_"
                    f"{capture_time}{suffix}"
                )
                if not cv2.imwrite(str(output), frame):
                    raise RuntimeError(f"保存图像失败: {output}")
                logger.info("Saved snapshot: %s", output)
            if show:
                if stream_type == "depth":
                    depth_mm = frame.astype(np.float32) * metadata["depth_scale"]
                    preview = cv2.convertScaleAbs(depth_mm, alpha=255.0 / 4000)
                    preview = cv2.applyColorMap(preview, cv2.COLORMAP_JET)
                else:
                    preview = frame
                cv2.imshow(f"{device_sn}/{stream_type}", preview)
        if show:
            cv2.waitKey(0)
    except (SnapshotRemoteError, TimeoutError, ValueError, RuntimeError) as exc:
        logger.error("拍照失败: %s", exc)
        return 1
    finally:
        if show:
            cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    device_sn = ""
    endpoint = "tcp://127.0.0.1:5559"
    stream_types = ("color", "depth")
    request_timeout_ms = 5000
    save_dir = None  # 例如 Path("photos")；depth 保存为原始 16 位 PNG
    show = True
    raise SystemExit(
        main(device_sn, endpoint, stream_types, request_timeout_ms, save_dir, show)
    )
