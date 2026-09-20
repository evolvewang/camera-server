"""One-shot requests for the latest cached color and/or depth frames."""

import json
import math
import threading
import time
from typing import Any, Optional

import zmq

from core.logger import get_logger
from service.frame_codec import encode_frame
from service.video_protocol import SNAPSHOT_PROTOCOL_VERSION


logger = get_logger(__name__)


class SnapshotRequestError(ValueError):
    """A request that can be answered with a structured error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ZmqSnapshotResponder:
    """Serve the latest camera packets on a dedicated ZeroMQ REP socket."""

    def __init__(
        self,
        camera: Any,
        endpoint: str,
        jpeg_quality: int,
        max_frame_age_ms: int,
        context: Optional[zmq.Context] = None,
    ):
        device_sn = getattr(camera, "device_sn", None)
        if not isinstance(device_sn, str) or not device_sn:
            raise ValueError("camera.device_sn 不能为空")
        stream_types = tuple(camera.stream_types)
        if not stream_types or any(
            name not in ("color", "depth") for name in stream_types
        ):
            raise ValueError("camera.stream_types 必须包含 color 或 depth")
        if not isinstance(endpoint, str) or not endpoint:
            raise ValueError("endpoint 不能为空")
        if (
            isinstance(jpeg_quality, bool)
            or not isinstance(jpeg_quality, int)
            or not 1 <= jpeg_quality <= 100
        ):
            raise ValueError("jpeg_quality 必须在 1 到 100 之间")
        if (
            isinstance(max_frame_age_ms, bool)
            or not isinstance(max_frame_age_ms, int)
            or max_frame_age_ms <= 0
        ):
            raise ValueError("max_frame_age_ms 必须是正整数")

        self.camera = camera
        self.device_sn = device_sn
        self.stream_types = stream_types
        self.endpoint = endpoint
        self.jpeg_quality = jpeg_quality
        self.max_frame_age_ms = max_frame_age_ms
        self._context = context
        self._owns_context = context is None
        self._running = threading.Event()
        self._startup_complete = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._error: Optional[BaseException] = None

    def start(self, startup_timeout: float = 5.0) -> None:
        """Bind on the worker thread and report startup errors to the caller."""
        if not isinstance(startup_timeout, (int, float)) or startup_timeout <= 0:
            raise ValueError("startup_timeout 必须是正数")
        if self._thread is not None and self._thread.is_alive():
            if self._running.is_set():
                return
            raise RuntimeError("拍照服务正在停止，请等待线程退出")
        self._error = None
        self._startup_complete.clear()
        self._running.set()
        self._thread = threading.Thread(
            target=self._serve,
            name=f"ZmqSnapshotResponder-{self.device_sn}",
            daemon=True,
        )
        self._thread.start()
        if not self._startup_complete.wait(startup_timeout):
            self.stop()
            raise TimeoutError(f"ZMQ 拍照服务在 {startup_timeout} 秒内未能启动")
        if self._error is not None:
            error = self._error
            self.stop()
            raise RuntimeError(f"ZMQ 拍照服务启动失败: {error}") from error
        logger.info("ZMQ snapshot service started: endpoint=%s", self.endpoint)

    def stop(self) -> None:
        """Stop the worker and release its socket and owned context."""
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                logger.warning("ZMQ snapshot worker did not stop within 2 seconds")
            else:
                self._thread = None

    @property
    def is_running(self) -> bool:
        return (
            self._running.is_set()
            and self._thread is not None
            and self._thread.is_alive()
        )

    @property
    def error(self) -> Optional[BaseException]:
        return self._error

    def _serve(self) -> None:
        context = self._context if self._context is not None else zmq.Context()
        socket = None
        try:
            socket = context.socket(zmq.REP)
            socket.setsockopt(zmq.LINGER, 0)
            socket.setsockopt(zmq.RCVHWM, 10)
            socket.setsockopt(zmq.SNDHWM, 10)
            socket.setsockopt(zmq.SNDTIMEO, 1000)
            socket.bind(self.endpoint)
            self._startup_complete.set()

            while self._running.is_set():
                if not socket.poll(100, zmq.POLLIN):
                    continue
                request = socket.recv_multipart()
                try:
                    reply = self._handle_request(request)
                except SnapshotRequestError as exc:
                    reply = self._error_reply(exc.code, str(exc))
                except Exception:
                    logger.exception("ZMQ snapshot request failed")
                    reply = self._error_reply("INTERNAL_ERROR", "拍照请求处理失败")
                socket.send_multipart(reply)
        except Exception as exc:
            self._error = exc
            logger.exception("ZMQ snapshot service failed: endpoint=%s", self.endpoint)
        finally:
            self._running.clear()
            self._startup_complete.set()
            if socket is not None:
                socket.close(linger=0)
            if self._owns_context:
                context.term()
            logger.info("ZMQ snapshot service stopped: endpoint=%s", self.endpoint)

    def _handle_request(self, parts: list[bytes]) -> list[bytes]:
        if len(parts) != 1:
            raise SnapshotRequestError("INVALID_REQUEST", "拍照请求必须只有一段 JSON")
        try:
            request = json.loads(parts[0].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SnapshotRequestError("INVALID_REQUEST", "拍照请求不是有效 JSON") from exc
        if not isinstance(request, dict):
            raise SnapshotRequestError("INVALID_REQUEST", "拍照请求必须是 JSON 对象")
        if type(request.get("protocol_version")) is not int or (
            request["protocol_version"] != SNAPSHOT_PROTOCOL_VERSION
        ):
            raise SnapshotRequestError("INVALID_REQUEST", "不支持的拍照协议版本")
        if request.get("device_sn") != self.device_sn:
            raise SnapshotRequestError("DEVICE_MISMATCH", "相机序列号不匹配")

        stream_types = request.get("stream_types")
        if (
            not isinstance(stream_types, list)
            or not stream_types
            or any(
                type(name) is not str or name not in ("color", "depth")
                for name in stream_types
            )
            or len(set(stream_types)) != len(stream_types)
        ):
            raise SnapshotRequestError(
                "INVALID_REQUEST", "stream_types 必须是非空且不重复的 color/depth 列表"
            )
        if any(name not in self.stream_types for name in stream_types):
            raise SnapshotRequestError("STREAM_UNAVAILABLE", "请求的相机流未启用")

        now = time.time()
        packets = []
        for stream_type in stream_types:
            packet = self.camera.get_frame_packet(stream_type)
            if packet is None:
                raise SnapshotRequestError("NO_FRAME", f"{stream_type} 尚无可用帧")
            if (
                isinstance(packet.timestamp, bool)
                or not isinstance(packet.timestamp, (int, float))
                or not math.isfinite(packet.timestamp)
                or now - packet.timestamp > self.max_frame_age_ms / 1000
            ):
                raise SnapshotRequestError("NO_FRAME", f"{stream_type} 的最新帧已过期")
            packets.append((stream_type, packet))

        metadata_list = []
        image_parts = []
        for stream_type, packet in packets:
            metadata, encoded = encode_frame(
                self.device_sn, stream_type, packet, self.jpeg_quality
            )
            metadata_list.append(metadata)
            image_parts.append(encoded)

        envelope = {
            "protocol_version": SNAPSHOT_PROTOCOL_VERSION,
            "status": "ok",
            "device_sn": self.device_sn,
            "frames": metadata_list,
        }
        return [self._json_bytes(envelope), *image_parts]

    @classmethod
    def _error_reply(cls, code: str, message: str) -> list[bytes]:
        return [
            cls._json_bytes({
                "protocol_version": SNAPSHOT_PROTOCOL_VERSION,
                "status": "error",
                "error": {"code": code, "message": message},
            })
        ]

    @staticmethod
    def _json_bytes(value: dict) -> bytes:
        return json.dumps(
            value, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
