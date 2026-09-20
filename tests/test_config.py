import unittest

from core.config import CONFIG_PATH, ROOT_DIR, load_config


class ProjectConfigTest(unittest.TestCase):
    def test_config_path_is_relative_to_project_root(self):
        self.assertEqual(CONFIG_PATH, ROOT_DIR / "configs" / "config.yaml")
        self.assertTrue(CONFIG_PATH.exists())

    def test_loads_repository_config_as_dictionary(self):
        config = load_config()

        self.assertIsInstance(config, dict)
        self.assertEqual(config["project"]["name"], "camera-server")
        self.assertTrue(config["camera"]["device_sn"])
        self.assertTrue(config["zmq"]["publisher_endpoint"].startswith("tcp://"))
        self.assertGreater(config["zmq"]["send_hwm"], 0)
        self.assertIn(
            config["stream"]["type"],
            {"color", "depth", "depth_and_color"},
        )
        self.assertIn(
            config["stream"]["align_mode"],
            {"DISABLE", "HW_D2C", "SW_D2C"},
        )
        self.assertEqual(config["stream"]["depth"]["format"], "Y16")
        self.assertGreater(config["stream"]["color"]["fps"], 0)
        self.assertGreater(config["stream"]["depth"]["fps"], 0)
        self.assertNotIn("subscriber_endpoint", config["zmq"])
        self.assertNotIn("receive_hwm", config["zmq"])
        self.assertNotIn("receive_timeout_ms", config["zmq"])
        self.assertNotIn("client", config)
        self.assertIn(
            config["logging"]["level"].upper(),
            {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"},
        )
        self.assertGreater(config["logging"]["backup_count"], 0)


if __name__ == "__main__":
    unittest.main()
