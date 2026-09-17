import threading
import time
import unittest
import uuid

import numpy as np
import zmq

from service.camera_client import ZmqVideoSubscriber
from service.zmq_publisher import ZmqVideoPublisher


class FakeCamera:
    def __init__(self):
        self._lock = threading.Lock()
        self._packet = None
        self._error = None

    def set_frame(self, frame_id, timestamp, frame):
        with self._lock:
            self._packet = (frame_id, timestamp, frame)

    def set_error(self, error):
        with self._lock:
            self._error = error

    def get_frame_packet(self):
        with self._lock:
            if self._error is not None:
                raise self._error
            return self._packet


class ZmqStreamTest(unittest.TestCase):
    def setUp(self):
        self.context = zmq.Context()
        self.endpoint = f"inproc://camera-stream-{uuid.uuid4()}"
        self.camera = FakeCamera()
        self.publisher = ZmqVideoPublisher(
            camera=self.camera,
            camera_id="TEST_CAM",
            endpoint=self.endpoint,
            jpeg_quality=90,
            context=self.context,
        )
        self.publisher.start()

    def tearDown(self):
        self.publisher.stop()
        self.context.term()

    def test_publishes_and_decodes_a_frame_with_metadata(self):
        subscriber = ZmqVideoSubscriber(
            endpoint=self.endpoint,
            camera_id="TEST_CAM",
            receive_timeout_ms=1000,
            context=self.context,
        )
        try:
            # Allow the SUB subscription to reach the PUB before publishing.
            time.sleep(0.05)
            frame = np.full((24, 32, 3), (10, 80, 160), dtype=np.uint8)
            self.camera.set_frame(7, 1234.5, frame)

            metadata, decoded = subscriber.receive()

            self.assertEqual(metadata["camera_id"], "TEST_CAM")
            self.assertEqual(metadata["frame_id"], 7)
            self.assertEqual(metadata["timestamp"], 1234.5)
            self.assertEqual(metadata["width"], 32)
            self.assertEqual(metadata["height"], 24)
            self.assertEqual(metadata["channels"], 3)
            self.assertEqual(metadata["encoding"], "jpeg")
            self.assertEqual(decoded.shape, frame.shape)
        finally:
            subscriber.close()

    def test_receive_times_out_when_camera_has_no_frame(self):
        subscriber = ZmqVideoSubscriber(
            endpoint=self.endpoint,
            camera_id="TEST_CAM",
            receive_timeout_ms=50,
            context=self.context,
        )
        try:
            with self.assertRaises(TimeoutError):
                subscriber.receive()
        finally:
            subscriber.close()

    def test_stop_is_idempotent(self):
        self.publisher.stop()
        self.publisher.stop()
        self.assertFalse(self.publisher.is_running)

    def test_camera_failure_is_exposed_by_publisher(self):
        self.camera.set_error(RuntimeError("fake capture failure"))
        deadline = time.monotonic() + 1.0
        while self.publisher.error is None and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertIsInstance(self.publisher.error, RuntimeError)
        self.assertFalse(self.publisher.is_running)


class PublisherValidationTest(unittest.TestCase):
    def test_rejects_invalid_jpeg_quality(self):
        with self.assertRaises(ValueError):
            ZmqVideoPublisher(FakeCamera(), jpeg_quality=0)

    def test_reports_bind_failure_from_start(self):
        publisher = ZmqVideoPublisher(
            FakeCamera(), endpoint="not-a-valid-zmq-transport://camera"
        )
        with self.assertRaises(RuntimeError):
            publisher.start()


if __name__ == "__main__":
    unittest.main()
