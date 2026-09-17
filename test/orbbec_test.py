import pyorbbecsdk as ob
import numpy as np
import cv2
import os
import time
from datetime import datetime

# ================================================================================== #

# 1. 枚举本机 Orbbec 相机
context = ob.Context()
device_list = context.query_devices()

print(f"Orbbec SDK 版本: {ob.get_version()}")
print(f"相机数量: {device_list.get_count()}")

for index in range(device_list.get_count()):
    print(f"\n========== Device {index} ==========")

    device = device_list.get_device_by_index(index)
    device_info = device.get_device_info()

    device_name = device_info.get_name()
    device_sn = device_info.get_serial_number()
    connection_type = device_info.get_connection_type()

    print(f"name={device_name}")
    print(f"sn={device_sn}")
    print(f"connection={connection_type}")


# ================================================================================== #

# # 2. 通过sn码连接相机
# device_sn = "CPC7B5300098"

# context = ob.Context()
# device_list = context.query_devices()

# available_sn = [
#     device_list.get_device_serial_number_by_index(index)
#     for index in range(device_list.get_count())
# ]

# if device_sn not in available_sn:
#     raise RuntimeError(f"没有找到相机 {device_sn}，当前设备: {available_sn}")

# device = device_list.get_device_by_serial_number(device_sn)
# device_info = device.get_device_info()

# print("名称:", device_info.get_name())
# print("序列号:", device_info.get_serial_number())
# print("固件版本:", device_info.get_firmware_version())
# print("硬件版本:", device_info.get_hardware_version())
# print("连接类型:", device_info.get_connection_type())
# print("VID:", hex(device_info.get_vid()))
# print("PID:", hex(device_info.get_pid()))
# print("UID:", device_info.get_uid())

# ================================================================================== #


# # 3. 视频流
# def color_frame_to_bgr(frame):
#     """
#     Orbbec ColorFrame -> OpenCV BGR ndarray
#     """
#     width = frame.get_width()
#     height = frame.get_height()
#     fmt = frame.get_format()

#     data = np.frombuffer(frame.get_data(), dtype=np.uint8)

#     if fmt == ob.OBFormat.MJPG:
#         # MJPEG 是压缩数据，需要解码
#         image = cv2.imdecode(data, cv2.IMREAD_COLOR)
#         return image

#     elif fmt == ob.OBFormat.RGB:
#         image = data.reshape((height, width, 3))
#         return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

#     elif fmt == ob.OBFormat.BGR:
#         return data.reshape((height, width, 3))

#     elif fmt == ob.OBFormat.YUYV:
#         image = data.reshape((height, width, 2))
#         return cv2.cvtColor(image, cv2.COLOR_YUV2BGR_YUY2)

#     else:
#         raise RuntimeError(f"暂不支持的彩色图格式: {fmt}")


# device_sn = "CPC7B5300098"

# context = ob.Context()
# device_list = context.query_devices()

# device = device_list.get_device_by_serial_number(device_sn)
# device_info = device.get_device_info()

# print("名称:", device_info.get_name())
# print("序列号:", device_info.get_serial_number())

# pipeline = ob.Pipeline(device)
# config = ob.Config()

# color_profiles = pipeline.get_stream_profile_list(ob.OBSensorType.COLOR_SENSOR)

# color_profile = color_profiles.get_default_video_stream_profile()

# config.enable_stream(color_profile)

# pipeline.start(config)

# save_dir = "/home/sunseed/algorithm/camera_data"

# SAVE_FPS = 5
# SAVE_DURATION = 10  # 秒

# os.makedirs(save_dir, exist_ok=True)

# save_interval = 1.0 / SAVE_FPS

# start_time = time.monotonic()
# next_save_time = start_time

# saved_count = 0

# try:
#     while True:
#         # 超过 10 秒退出
#         now = time.monotonic()

#         if now - start_time >= SAVE_DURATION:
#             break

#         frames = pipeline.wait_for_frames(1000)

#         if frames is None:
#             continue

#         color_frame = frames.get_color_frame()

#         if color_frame is None:
#             continue

#         image = color_frame_to_bgr(color_frame)

#         if image is None:
#             continue

#         # 到达保存时间点
#         now = time.monotonic()

#         if now >= next_save_time:
#             timestamp = datetime.now().strftime(
#                 "%Y-%m-%d_%H-%M-%S.%f"
#             )[:-3]

#             save_path = os.path.join(
#                 save_dir,
#                 f"{timestamp}.jpg"
#             )

#             success = cv2.imwrite(save_path, image)

#             if success:
#                 saved_count += 1
#                 print(
#                     f"[{saved_count}] "
#                     f"保存: {save_path}"
#                 )

#             # 下一个固定时间点
#             next_save_time += save_interval

# finally:
#     pipeline.stop()

# print(f"采集完成，共保存 {saved_count} 张图片")