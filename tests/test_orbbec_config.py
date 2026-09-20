import copy
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pyorbbecsdk as ob

from camera.orbbec_camera import OrbbecCamera
from core.config import app_config


class OrbbecConfigTest(unittest.TestCase):
    def setUp(self):
        self.settings = copy.deepcopy(app_config["stream"])
        self.device = MagicMock()
        self.device.is_property_supported.return_value = True
        self.device.get_int_property_range.return_value = SimpleNamespace(
            min=0, max=20000, step=1
        )
        info = self.device.get_device_info.return_value
        info.get_name.return_value = "Fake Orbbec"
        info.get_serial_number.return_value = "FAKE_SN"
        info.get_firmware_version.return_value = "1.0"
        info.get_connection_type.return_value = "USB"

        devices = MagicMock()
        devices.get_count.return_value = 1
        devices.get_device_serial_number_by_index.return_value = "FAKE_SN"
        devices.get_device_by_serial_number.return_value = self.device
        self.context_patch = patch("camera.orbbec_camera.ob.Context")
        self.pipeline_patch = patch("camera.orbbec_camera.ob.Pipeline")
        self.config_patch = patch("camera.orbbec_camera.ob.Config")
        self.context_class = self.context_patch.start()
        self.pipeline_class = self.pipeline_patch.start()
        self.config_class = self.config_patch.start()
        self.addCleanup(self.context_patch.stop)
        self.addCleanup(self.pipeline_patch.stop)
        self.addCleanup(self.config_patch.stop)
        self.context_class.return_value.query_devices.return_value = devices
        self.pipeline = self.pipeline_class.return_value
        self.config = self.config_class.return_value
        self.color_profile = MagicMock()
        self.depth_profile = MagicMock()
        color_profiles = MagicMock()
        depth_profiles = MagicMock()
        color_profiles.get_video_stream_profile.return_value = self.color_profile
        depth_profiles.get_video_stream_profile.return_value = self.depth_profile

        def select_profiles(sensor_type):
            if sensor_type == ob.OBSensorType.COLOR_SENSOR:
                return color_profiles
            return depth_profiles

        self.pipeline.get_stream_profile_list.side_effect = select_profiles
        self.color_profiles = color_profiles
        self.depth_profiles = depth_profiles

    def test_dual_stream_selects_profiles_and_sets_auto_controls(self):
        camera = OrbbecCamera("FAKE_SN", self.settings)
        self.assertEqual(camera.stream_types, ("color", "depth"))
        self.assertEqual(self.config.enable_stream.call_count, 2)
        self.config.set_align_mode.assert_called_once_with(ob.OBAlignMode.DISABLE)
        self.color_profiles.get_video_stream_profile.assert_called_once_with(
            1280, 720, ob.OBFormat.MJPG, 30
        )
        self.depth_profiles.get_video_stream_profile.assert_called_once_with(
            1280, 720, ob.OBFormat.Y16, 30
        )
        self.device.set_bool_property.assert_any_call(
            ob.OBPropertyID.OB_PROP_COLOR_AUTO_EXPOSURE_BOOL, True
        )
        self.device.set_bool_property.assert_any_call(
            ob.OBPropertyID.OB_PROP_DEPTH_AUTO_EXPOSURE_BOOL, True
        )

    def test_manual_exposure_disables_auto_before_setting_value(self):
        self.settings["type"] = "color"
        self.settings["color"]["auto_exposure"] = False
        self.settings["color"]["exposure"] = 10010
        OrbbecCamera("FAKE_SN", self.settings)
        calls = self.device.mock_calls
        auto_call = unittest.mock.call.set_bool_property(
            ob.OBPropertyID.OB_PROP_COLOR_AUTO_EXPOSURE_BOOL, False
        )
        exposure_call = unittest.mock.call.set_int_property(
            ob.OBPropertyID.OB_PROP_COLOR_EXPOSURE_INT, 10010
        )
        self.assertLess(calls.index(auto_call), calls.index(exposure_call))

    def test_rejects_manual_exposure_while_auto_is_enabled(self):
        self.settings["color"]["exposure"] = 10010
        with self.assertRaisesRegex(ValueError, "关闭自动曝光"):
            OrbbecCamera("FAKE_SN", self.settings)

    def test_rejects_exposure_outside_device_range(self):
        self.settings["type"] = "color"
        self.settings["color"]["auto_exposure"] = False
        self.settings["color"]["exposure"] = 30000
        with self.assertRaisesRegex(ValueError, "超出设备范围"):
            OrbbecCamera("FAKE_SN", self.settings)

    def test_rejects_unsupported_requested_control(self):
        self.settings["type"] = "color"
        self.device.is_property_supported.return_value = False
        with self.assertRaisesRegex(RuntimeError, "不支持写入"):
            OrbbecCamera("FAKE_SN", self.settings)

    def test_rejects_align_mode_without_both_streams(self):
        self.settings["type"] = "depth"
        self.settings["align_mode"] = "HW_D2C"
        with self.assertRaisesRegex(ValueError, "同时开启"):
            OrbbecCamera("FAKE_SN", self.settings)

    def test_capture_keeps_color_and_raw_depth_with_scale(self):
        camera = OrbbecCamera("FAKE_SN", self.settings)
        color_data = np.arange(12, dtype=np.uint8).reshape((2, 2, 3))
        depth_data = np.array([[0, 100], [1000, 65535]], dtype=np.uint16)
        color_frame = MagicMock()
        color_frame.get_width.return_value = 2
        color_frame.get_height.return_value = 2
        color_frame.get_format.return_value = ob.OBFormat.BGR
        color_frame.get_data.return_value = color_data.tobytes()
        depth_frame = MagicMock()
        depth_frame.get_width.return_value = 2
        depth_frame.get_height.return_value = 2
        depth_frame.get_format.return_value = ob.OBFormat.Y16
        depth_frame.get_data.return_value = depth_data.tobytes()
        depth_frame.get_depth_scale.return_value = 0.1
        frames = MagicMock()
        frames.get_color_frame.return_value = color_frame
        frames.get_depth_frame.return_value = depth_frame

        def one_frame(timeout):
            camera._running.clear()
            return frames

        self.pipeline.wait_for_frames.side_effect = one_frame
        camera._running.set()
        camera._capture_loop()

        color_packet = camera.get_frame_packet("color")
        depth_packet = camera.get_frame_packet("depth")
        self.assertEqual(color_packet.frame_id, 1)
        self.assertEqual(depth_packet.frame_id, 1)
        self.assertEqual(depth_packet.depth_scale, 0.1)
        np.testing.assert_array_equal(color_packet.frame, color_data)
        np.testing.assert_array_equal(depth_packet.frame, depth_data)


if __name__ == "__main__":
    unittest.main()
