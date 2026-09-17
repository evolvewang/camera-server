"""Low-latency JPEG-over-ZeroMQ video publisher."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

import cv2
import zmq


LOGGER = logging.getLogger(__name__)


class ZmqVideoPublisher:
    """Publish each new camera frame as ``topic, metadata JSON, JPEG``."""

    def __init__(
        self,
        camera,
        camera_id: str = "CAM_01",
        endpoint: str = "tcp://0.0.0.0:5558",
        jpeg_quality: int = 85,
        context: Optional[zmq.Context] = None,
    ):
        if not camera_id:
            raise ValueError("camera_id 不能为空")
        if not 1 <= jpeg_quality <= 100:
            raise ValueError("jpeg_quality 必须在 1 到 100 之间")

        self.camera = camera
        self.camera_id = camera_id
        self.endpoint = endpoint
        self.jpeg_quality = jpeg_quality
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
        if self._running.is_set():
            return

        self._error = None
        self._startup_complete.clear()
        self._running.set()
        self._thread = threading.Thread(
            target=self._publish_loop,
            daemon=True,
            name=f"ZmqVideoPublisher-{self.camera_id}",
        )
        self._thread.start()

        if not self._startup_complete.wait(startup_timeout):
            self.stop()
            raise TimeoutError(f"ZMQ 发布端在 {startup_timeout} 秒内未能启动")
        if self._error is not None:
            error = self._error
            self.stop()
            raise RuntimeError(f"ZMQ 发布端启动失败: {error}") from error

        LOGGER.info(
            "ZMQ video stream started: endpoint=%s camera=%s quality=%s",
            self.endpoint,
            self.camera_id,
            self.jpeg_quality,
        )

    def stop(self) -> None:
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                LOGGER.warning("ZMQ publisher did not stop within 2 seconds")
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
        context = self._context or zmq.Context()
        socket = None
        try:
            socket = context.socket(zmq.PUB)
            socket.setsockopt(zmq.SNDHWM, 2)
            socket.setsockopt(zmq.LINGER, 0)
            socket.bind(self.endpoint)
            self._startup_complete.set()

            last_frame_id = -1
            while self._running.is_set():
                packet = self.camera.get_frame_packet()
                if packet is None:
                    time.sleep(0.005)
                    continue

                frame_id, timestamp, frame = packet
                if frame_id == last_frame_id:
                    time.sleep(0.001)
                    continue
                last_frame_id = frame_id

                success, encoded = cv2.imencode(
                    ".jpg",
                    frame,
                    [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality],
                )
                if not success:
                    LOGGER.warning(
                        "JPEG encoding failed: camera=%s frame=%s",
                        self.camera_id,
                        frame_id,
                    )
                    continue

                metadata = {
                    "camera_id": self.camera_id,
                    "frame_id": frame_id,
                    "timestamp": timestamp,
                    "width": frame.shape[1],
                    "height": frame.shape[0],
                    "channels": frame.shape[2],
                    "encoding": "jpeg",
                }
                socket.send_multipart(
                    [
                        self.camera_id.encode("utf-8"),
                        json.dumps(metadata, separators=(",", ":")).encode("utf-8"),
                        encoded.tobytes(),
                    ]
                )
        except Exception as exc:
            self._running.clear()
            self._error = exc
            LOGGER.exception("ZMQ publisher failed: endpoint=%s", self.endpoint)
        finally:
            self._running.clear()
            self._startup_complete.set()
            if socket is not None:
                socket.close(linger=0)
            if self._owns_context:
                context.term()
            LOGGER.info("ZMQ video stream stopped: endpoint=%s", self.endpoint)
