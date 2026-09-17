import tempfile
import unittest
from pathlib import Path

from core.config import CONFIG_PATH, ConfigError, load_config


class ProjectConfigTest(unittest.TestCase):
    def test_loads_repository_config(self):
        config = load_config(CONFIG_PATH)

        self.assertEqual(config.project.name, "camera-server")
        self.assertTrue(config.camera.device_sn)
        self.assertGreaterEqual(config.stream.jpeg_quality, 1)
        self.assertLessEqual(config.stream.jpeg_quality, 100)
        self.assertTrue(config.zmq.publisher_endpoint.startswith("tcp://"))
        self.assertTrue(config.zmq.subscriber_endpoint.startswith("tcp://"))
        self.assertGreater(config.zmq.send_hwm, 0)
        self.assertGreater(config.zmq.receive_hwm, 0)
        self.assertGreater(config.zmq.receive_timeout_ms, 0)
        self.assertIsInstance(config.client.show, bool)
        self.assertIn(
            config.logging.level,
            {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"},
        )
        self.assertTrue(config.logging.directory)
        self.assertTrue(config.logging.filename)
        self.assertGreater(config.logging.backup_count, 0)

    def test_rejects_unknown_configuration_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text("unexpected: true\n", encoding="utf-8")

            with self.assertRaises(ConfigError):
                load_config(config_path)

    def test_reports_missing_configuration_file(self):
        with self.assertRaises(ConfigError):
            load_config("missing-camera-server-config.yaml")


if __name__ == "__main__":
    unittest.main()
