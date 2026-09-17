import time
import threading

import cv2
import numpy as np
import pyorbbecsdk as ob


class OrbbecCamera:
    def __init__(self, device_sn: str):
        self.device_sn = device_sn

        # Orbbec
        self.context = None
        self.device = None
        self.pipeline = None
        self.config = None

        # 线程控制
        self._running = False
        self._thread = None

        # 最新帧
        self._frame = None
        self._frame_lock = threading.Lock()

        # 基本状态
        self._frame_id = 0
        self._timestamp = None

        self._init_camera()

    def _init_camera(self):
        """
        初始化相机、Pipeline 和 Color Stream
        """
        self.context = ob.Context()
        device_list = self.context.query_devices()

        available_sn = [device_list.get_device_serial_number_by_index(index) for index in range(device_list.get_count())]

        if self.device_sn not in available_sn:
            raise RuntimeError(f"没有找到相机 {self.device_sn}，当前设备: {available_sn}")

        # 根据 SN 获取指定相机
        self.device = device_list.get_device_by_serial_number(self.device_sn)

        device_info = self.device.get_device_info()

        print("名称:", device_info.get_name())
        print("序列号:", device_info.get_serial_number())
        print("固件版本:", device_info.get_firmware_version())
        print("连接类型:", device_info.get_connection_type())

        # 创建 Pipeline
        self.pipeline = ob.Pipeline(self.device)
        self.config = ob.Config()

        # 获取 Color Stream
        color_profiles = self.pipeline.get_stream_profile_list(ob.OBSensorType.COLOR_SENSOR)

        color_profile = (color_profiles.get_default_video_stream_profile())

        print(
            "Color Stream:",
            f"{color_profile.get_width()}x"
            f"{color_profile.get_height()}",
            f"@ {color_profile.get_fps()} FPS",
            f"format={color_profile.get_format()}",
        )

        self.config.enable_stream(color_profile)

    def start(self):
        """
        启动相机，并创建后台采集线程。
        """
        if self._running:
            return

        self.pipeline.start(self.config)

        self._running = True

        self._thread = threading.Thread(
            target=self._capture_loop,
            name=f"OrbbecCamera-{self.device_sn}",
            daemon=True,
        )

        self._thread.start()

        print(f"Orbbec Camera {self.device_sn} started.")

    def stop(self):
        """
        停止采集线程和 Pipeline。
        """
        if not self._running:
            return

        self._running = False

        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

        self.pipeline.stop()

        with self._frame_lock:
            self._frame = None

        print(f"Orbbec Camera {self.device_sn} stopped.")

    def _capture_loop(self):
        """
        后台线程持续读取相机。

        相机只在这个线程中调用 wait_for_frames()。
        """
        while self._running:
            try:
                frames = self.pipeline.wait_for_frames(1000)

                if frames is None:
                    continue

                color_frame = frames.get_color_frame()

                if color_frame is None:
                    continue

                image = self._color_frame_to_bgr(color_frame)

                if image is None:
                    continue

                timestamp = time.time()

                # 这里只进行非常快速的引用替换
                with self._frame_lock:
                    self._frame = image
                    self._frame_id += 1
                    self._timestamp = timestamp

            except Exception as e:
                if self._running:
                    print(f"Orbbec 采集异常: {e}")

    def get_frame(self):
        """
        获取当前最新一帧。

        Returns
        -------
        np.ndarray | None
            OpenCV BGR 图像。
            相机尚未产生有效帧时返回 None。
        """
        with self._frame_lock:
            return self._frame

    @property
    def frame_id(self):
        with self._frame_lock:
            return self._frame_id

    @property
    def timestamp(self):
        with self._frame_lock:
            return self._timestamp

    @property
    def is_running(self):
        return self._running

    @staticmethod
    def _color_frame_to_bgr(frame):
        """
        Orbbec ColorFrame -> OpenCV BGR ndarray
        """
        width = frame.get_width()
        height = frame.get_height()
        fmt = frame.get_format()

        data = np.frombuffer(
            frame.get_data(),
            dtype=np.uint8,
        )

        if fmt == ob.OBFormat.MJPG:
            return cv2.imdecode(
                data,
                cv2.IMREAD_COLOR,
            )

        elif fmt == ob.OBFormat.RGB:
            image = data.reshape(
                (height, width, 3)
            )

            return cv2.cvtColor(
                image,
                cv2.COLOR_RGB2BGR,
            )

        elif fmt == ob.OBFormat.BGR:
            return data.reshape(
                (height, width, 3)
            )

        elif fmt == ob.OBFormat.YUYV:
            image = data.reshape(
                (height, width, 2)
            )

            return cv2.cvtColor(
                image,
                cv2.COLOR_YUV2BGR_YUY2,
            )

        else:
            raise RuntimeError(
                f"暂不支持的彩色图格式: {fmt}"
            )


if __name__ == "__main__":
    camera = OrbbecCamera(device_sn="CPC7B5300098")

    camera.start()

    try:
        while True:
            frame = camera.get_frame()

            if frame is None:
                time.sleep(0.01)
                continue

            print(
                f"frame_id={camera.frame_id}, "
                f"shape={frame.shape}, "
                f"dtype={frame.dtype}"
            )

            # 这里可以直接使用 frame
            #
            # result = model(frame)
            # cv2.imwrite(...)
            #
            # 后续 ZMQ Server 也是在这里：
            # frame = camera.get_frame()
            # 然后发送 frame

            time.sleep(1)

    except KeyboardInterrupt:
        print("退出程序")

    finally:
        camera.stop()