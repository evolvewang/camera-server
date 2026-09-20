"""Low-latency color JPEG and lossless depth PNG-over-ZeroMQ publisher."""

import json
import threading
import time
from typing import Any, Optional

import zmq

from core.logger import get_logger
from service.frame_codec import FrameEncodingError, encode_frame


logger = get_logger(__name__)


class ZmqVideoPublisher:
    """Publish each new frame as ``SN topic, metadata JSON, image bytes``."""

    def __init__(
        self,
        camera: Any,
        endpoint: str,
        jpeg_quality: int,
        send_hwm: int,
        context: Optional[zmq.Context] = None,
    ):
        device_sn = getattr(camera, "device_sn", None)
        if not isinstance(device_sn, str) or not device_sn:
            raise ValueError("camera.device_sn 不能为空")
        if (
            isinstance(jpeg_quality, bool)
            or not isinstance(jpeg_quality, int)
            or not 1 <= jpeg_quality <= 100
        ):
            raise ValueError("jpeg_quality 必须在 1 到 100 之间")
        if (
            isinstance(send_hwm, bool)
            or not isinstance(send_hwm, int)
            or send_hwm <= 0
        ):
            raise ValueError("send_hwm 必须是正整数")

        self.camera = camera
        self.device_sn = device_sn
        self.stream_types = tuple(camera.stream_types)
        if not self.stream_types or any(
            name not in ("color", "depth") for name in self.stream_types
        ):
            raise ValueError("camera.stream_types 必须包含 color 或 depth")
        self.endpoint = endpoint
        self.jpeg_quality = jpeg_quality
        self.send_hwm = send_hwm
        self._context = context
        self._owns_context = context is None

        self._running = threading.Event()
        self._startup_complete = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._error: Optional[BaseException] = None

    def start(self, startup_timeout: float = 5.0) -> None:
        """Bind the endpoint and start publishing.

        Binding happens in the worker because a ZeroMQ socket must only be used
        by the thread that created it. This method still waits for the bind
        result, so address/configuration failures are reported to the caller.
        """
        if not isinstance(startup_timeout, (int, float)) or startup_timeout <= 0:
            raise ValueError("startup_timeout 必须是正数")
        if self._running.is_set():
            return

        self._error = None
        self._startup_complete.clear()
        self._running.set()
        self._thread = threading.Thread(
            target=self._publish_loop,
            daemon=True,
            name=f"ZmqVideoPublisher-{self.device_sn}",
        )
        self._thread.start()

        if not self._startup_complete.wait(startup_timeout):
            self.stop()
            raise TimeoutError(f"ZMQ 发布端在 {startup_timeout} 秒内未能启动")
        if self._error is not None:
            error = self._error
            self.stop()
            raise RuntimeError(f"ZMQ 发布端启动失败: {error}") from error

        logger.info(
            "ZMQ video stream started: endpoint=%s device_sn=%s quality=%s",
            self.endpoint,
            self.device_sn,
            self.jpeg_quality,
        )

    def stop(self) -> None:
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                logger.warning("ZMQ publisher did not stop within 2 seconds")
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

    def _publish_loop(self) -> None:
        context = self._context if self._context is not None else zmq.Context()
        socket = None
        try:
            socket = context.socket(zmq.PUB)
            socket.setsockopt(zmq.SNDHWM, self.send_hwm)
            socket.setsockopt(zmq.LINGER, 0)
            socket.bind(self.endpoint)
            self._startup_complete.set()

            last_frame_ids = {name: -1 for name in self.stream_types}
            while self._running.is_set():
                sent = False
                for stream_type in self.stream_types:
                    packet = self.camera.get_frame_packet(stream_type)
                    if packet is None or packet.frame_id == last_frame_ids[stream_type]:
                        continue
                    last_frame_ids[stream_type] = packet.frame_id
                    try:
                        metadata, encoded = encode_frame(
                            self.device_sn, stream_type, packet, self.jpeg_quality
                        )
                    except FrameEncodingError:
                        logger.warning(
                            "Image encoding failed: serial=%s stream=%s frame=%s",
                            self.device_sn, stream_type, packet.frame_id,
                        )
                        continue
                    socket.send_multipart([
                        self.device_sn.encode("utf-8"),
                        json.dumps(metadata, separators=(",", ":")).encode("utf-8"),
                        encoded,
                    ])
                    sent = True
                if not sent:
                    time.sleep(0.005)
        except Exception as exc:
            self._running.clear()
            self._error = exc
            logger.exception("ZMQ publisher failed: endpoint=%s", self.endpoint)
        finally:
            self._running.clear()
            self._startup_complete.set()
            if socket is not None:
                socket.close(linger=0)
            if self._owns_context:
                context.term()
            logger.info("ZMQ video stream stopped: endpoint=%s", self.endpoint)
