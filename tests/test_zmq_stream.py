import threading
import time
import unittest
import uuid

import numpy as np
import zmq

from camera.orbbec_camera import FramePacket
from zmq_client import ZmqVideoSubscriber
from service.zmq_publisher import ZmqVideoPublisher


class FakeCamera:
    def __init__(self, device_sn="TEST_SN", stream_types=("color", "depth")):
        self.device_sn = device_sn
        self.stream_types = stream_types
        self._lock = threading.Lock()
        self._packets = {name: None for name in stream_types}
        self._error = None

    def set_frame(self, stream_type, frame_id, timestamp, frame, depth_scale=None):
        with self._lock:
            self._packets[stream_type] = FramePacket(
                frame_id, timestamp, frame, depth_scale
            )

    def set_error(self, error):
        with self._lock:
            self._error = error

    def get_frame_packet(self, stream_type):
        with self._lock:
            if self._error is not None:
                raise self._error
            return self._packets[stream_type]


class ZmqStreamTest(unittest.TestCase):
    def setUp(self):
        self.context = zmq.Context()
        self.endpoint = f"inproc://camera-stream-{uuid.uuid4()}"
        self.camera = FakeCamera()
        self.publisher = ZmqVideoPublisher(
            camera=self.camera,
            endpoint=self.endpoint,
            jpeg_quality=90,
            send_hwm=2,
            context=self.context,
        )
        self.publisher.start()

    def tearDown(self):
        self.publisher.stop()
        self.context.term()

    def test_publishes_and_decodes_a_frame_with_metadata(self):
        subscriber = ZmqVideoSubscriber(
            device_sn="TEST_SN",
            endpoint=self.endpoint,
            receive_timeout_ms=1000,
            receive_hwm=2,
            context=self.context,
        )
        try:
            # Allow the SUB subscription to reach the PUB before publishing.
            time.sleep(0.05)
            frame = np.full((24, 32, 3), (10, 80, 160), dtype=np.uint8)
            self.camera.set_frame("color", 7, 1234.5, frame)

            metadata, decoded = subscriber.receive()

            self.assertEqual(metadata["protocol_version"], 2)
            self.assertEqual(metadata["device_sn"], "TEST_SN")
            self.assertEqual(metadata["stream_type"], "color")
            self.assertEqual(metadata["frame_id"], 7)
            self.assertEqual(metadata["timestamp"], 1234.5)
            self.assertEqual(metadata["width"], 32)
            self.assertEqual(metadata["height"], 24)
            self.assertEqual(metadata["channels"], 3)
            self.assertEqual(metadata["encoding"], "jpeg")
            self.assertEqual(decoded.shape, frame.shape)
        finally:
            subscriber.close()

    def test_publishes_lossless_depth_with_scale(self):
        subscriber = ZmqVideoSubscriber(
            device_sn="TEST_SN",
            endpoint=self.endpoint,
            receive_timeout_ms=1000,
            receive_hwm=2,
            context=self.context,
        )
        try:
            time.sleep(0.05)
            depth = np.array([[0, 1234], [4500, 65535]], dtype=np.uint16)
            self.camera.set_frame("depth", 3, 1234.6, depth, 0.1)
            metadata, decoded = subscriber.receive()
            self.assertEqual(metadata["stream_type"], "depth")
            self.assertEqual(metadata["encoding"], "png16")
            self.assertEqual(metadata["channels"], 1)
            self.assertEqual(metadata["depth_scale"], 0.1)
            self.assertEqual(decoded.dtype, np.uint16)
            np.testing.assert_array_equal(decoded, depth)
        finally:
            subscriber.close()

    def test_publishes_both_streams_with_same_sn_topic(self):
        subscriber = ZmqVideoSubscriber(
            device_sn="TEST_SN",
            endpoint=self.endpoint,
            receive_timeout_ms=1000,
            receive_hwm=2,
            context=self.context,
        )
        try:
            time.sleep(0.05)
            self.camera.set_frame(
                "color", 1, 10.0, np.zeros((8, 8, 3), dtype=np.uint8)
            )
            self.camera.set_frame(
                "depth", 1, 10.0, np.ones((8, 8), dtype=np.uint16), 1.0
            )
            received = [subscriber.receive()[0]["stream_type"] for _ in range(2)]
            self.assertEqual(set(received), {"color", "depth"})
        finally:
            subscriber.close()

    def test_receive_times_out_when_camera_has_no_frame(self):
        subscriber = ZmqVideoSubscriber(
            device_sn="TEST_SN",
            endpoint=self.endpoint,
            receive_timeout_ms=50,
            receive_hwm=2,
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

    def test_invalid_depth_scale_stops_publisher(self):
        self.camera.set_frame(
            "depth", 1, 10.0, np.ones((2, 2), dtype=np.uint16), 0
        )
        deadline = time.monotonic() + 1.0
        while self.publisher.error is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsInstance(self.publisher.error, ValueError)
        self.assertFalse(self.publisher.is_running)


class PublisherValidationTest(unittest.TestCase):
    def test_rejects_camera_without_device_sn(self):
        with self.assertRaises(ValueError):
            ZmqVideoPublisher(
                FakeCamera(device_sn=""),
                endpoint="inproc://invalid-camera",
                jpeg_quality=85,
                send_hwm=2,
            )

    def test_rejects_invalid_jpeg_quality(self):
        with self.assertRaises(ValueError):
            ZmqVideoPublisher(
                FakeCamera(),
                endpoint="inproc://invalid-quality",
                jpeg_quality=0,
                send_hwm=2,
            )

    def test_reports_bind_failure_from_start(self):
        publisher = ZmqVideoPublisher(
            FakeCamera(),
            endpoint="not-a-valid-zmq-transport://camera",
            jpeg_quality=85,
            send_hwm=2,
        )
        with self.assertRaises(RuntimeError):
            publisher.start()


if __name__ == "__main__":
    unittest.main()
