import cv2
import json
import zmq
import numpy as np


class ZmqVideoSubscriber:

    def __init__(
        self,
        endpoint="tcp://127.0.0.1:5558",
        camera_id="CAM_01",
    ):
        self.endpoint = endpoint
        self.camera_id = camera_id

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.SUB)

        # 实时视频场景，避免积压太多旧帧
        self.socket.setsockopt(zmq.RCVHWM, 2)

        # 订阅指定 camera_id
        self.socket.setsockopt_string(
            zmq.SUBSCRIBE,
            self.camera_id,
        )

        self.socket.connect(self.endpoint)

        print(
            f"Connected to ZMQ video stream: "
            f"{self.endpoint}, "
            f"camera={self.camera_id}"
        )

    def receive(self):

        topic, metadata_bytes, image_bytes = (
            self.socket.recv_multipart()
        )

        metadata = json.loads(
            metadata_bytes.decode("utf-8")
        )

        image_array = np.frombuffer(
            image_bytes,
            dtype=np.uint8,
        )

        frame = cv2.imdecode(
            image_array,
            cv2.IMREAD_COLOR,
        )

        if frame is None:
            return None

        return metadata, frame

    def close(self):
        self.socket.close()
        self.context.term()


if __name__ == "__main__":

    subscriber = ZmqVideoSubscriber(
        endpoint="tcp://127.0.0.1:5558",
        camera_id="CAM_01",
    )

    try:

        while True:

            result = subscriber.receive()

            if result is None:
                continue

            metadata, frame = result

            print(
                f"frame_id={metadata['frame_id']}, "
                f"timestamp={metadata['timestamp']}, "
                f"shape={frame.shape}"
            )


    except KeyboardInterrupt:
        pass

    finally:
        subscriber.close()
        cv2.destroyAllWindows()