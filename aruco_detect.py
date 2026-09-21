"""ArUco detection helpers built on the shared video subscriber."""

from functools import lru_cache
from typing import Optional

import cv2
import numpy as np
import zmq

from core.logger import get_logger
from zmq_client import ZmqVideoSubscriber as _ZmqVideoSubscriber


logger = get_logger(__name__)


@lru_cache(maxsize=8)
def _get_aruco_detector(dictionary_name: str):
    """Create and cache an OpenCV ArUco detector for a predefined dictionary."""
    aruco = getattr(cv2, "aruco", None)
    if aruco is None:
        raise RuntimeError(
            "当前 OpenCV 不包含 cv2.aruco，请安装带 ArUco 模块的 OpenCV"
        )

    dictionary_id = getattr(aruco, dictionary_name, None)
    if dictionary_id is None:
        raise ValueError(f"不支持的 ArUco 字典: {dictionary_name}")

    dictionary = aruco.getPredefinedDictionary(dictionary_id)
    parameters = aruco.DetectorParameters()
    if hasattr(aruco, "ArucoDetector"):
        return "modern", aruco.ArucoDetector(dictionary, parameters)
    return "legacy", (aruco, dictionary, parameters)


def _to_aruco_gray(frame: np.ndarray) -> np.ndarray:
    """Convert a BGR or grayscale frame to the uint8 image ArUco expects."""
    if not isinstance(frame, np.ndarray) or frame.size == 0:
        raise ValueError("frame 必须是非空 numpy.ndarray")
    if frame.ndim == 3:
        if frame.shape[2] != 3:
            raise ValueError("彩色 frame 必须是 3 通道 BGR 图像")
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    elif frame.ndim == 2:
        gray = frame
    else:
        raise ValueError("frame 必须是二维灰度图或三维 BGR 图像")

    if gray.dtype != np.uint8:
        raise ValueError("ArUco 识别要求 uint8 图像，建议传入 color 流")
    return gray


def _detect_aruco_markers(
    frame: np.ndarray,
    dictionary_name: str,
) -> tuple[list[np.ndarray], list[int]]:
    """Return detected marker corners and IDs in OpenCV detection order."""
    gray = _to_aruco_gray(frame)
    detector_type, detector = _get_aruco_detector(dictionary_name)
    if detector_type == "modern":
        corners, ids, _ = detector.detectMarkers(gray)
    else:
        aruco, dictionary, parameters = detector
        corners, ids, _ = aruco.detectMarkers(
            gray,
            dictionary,
            parameters=parameters,
        )
    marker_ids = [] if ids is None else [int(marker_id[0]) for marker_id in ids]
    return corners, marker_ids


def _centers_from_corners(corners: list[np.ndarray]) -> list[tuple[int, int]]:
    centers = []
    for marker_corners in corners:
        points = np.asarray(marker_corners, dtype=np.float32).reshape(4, 2)
        center_x, center_y = np.mean(points, axis=0)
        centers.append((int(round(float(center_x))), int(round(float(center_y)))))
    return centers


def detect_aruco_centers(
    frame: np.ndarray,
    dictionary_name: str = "DICT_4X4_50",
) -> list[tuple[int, int]]:
    """Detect ArUco markers and return their pixel centers as ``(x, y)``."""
    corners, _ = _detect_aruco_markers(frame, dictionary_name)
    return _centers_from_corners(corners)


def draw_aruco_annotations(
    frame: np.ndarray,
    corners: list[np.ndarray],
    marker_ids: list[int],
    centers: list[tuple[int, int]],
) -> np.ndarray:
    """Return a copy of a color frame with ArUco boxes, IDs and centers drawn."""
    annotated = frame.copy()
    if corners:
        marker_ids_array = np.asarray(marker_ids, dtype=np.int32).reshape(-1, 1)
        cv2.aruco.drawDetectedMarkers(annotated, corners, marker_ids_array)

    for index, (center_x, center_y) in enumerate(centers):
        cv2.drawMarker(
            annotated,
            (center_x, center_y),
            (0, 0, 255),
            cv2.MARKER_CROSS,
            20,
            2,
        )
        marker_id = marker_ids[index] if index < len(marker_ids) else "?"
        label = f"ID:{marker_id} center=({center_x},{center_y})"
        cv2.putText(
            annotated,
            label,
            (center_x + 12, max(20, center_y - 12)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )
    return annotated


class ZmqVideoSubscriber(_ZmqVideoSubscriber):
    """Shared video subscriber with optional ArUco detection on color frames."""

    def __init__(
        self,
        device_sn: str,
        endpoint: str,
        receive_timeout_ms: Optional[int],
        receive_hwm: int,
        context: Optional[zmq.Context] = None,
    ) -> None:
        super().__init__(
            device_sn=device_sn,
            endpoint=endpoint,
            receive_timeout_ms=receive_timeout_ms,
            receive_hwm=receive_hwm,
            context=context,
        )
        self.last_aruco_corners: list[np.ndarray] = []
        self.last_aruco_ids: list[int] = []

    def receive_with_aruco(
        self,
        dictionary_name: str = "DICT_4X4_50",
    ) -> tuple[dict, np.ndarray, list[tuple[int, int]]]:
        """Receive one frame and detect centers on color frames."""
        metadata, frame = self.receive()
        if metadata["stream_type"] != "color":
            self.last_aruco_corners = []
            self.last_aruco_ids = []
            return metadata, frame, []

        corners, marker_ids = _detect_aruco_markers(frame, dictionary_name)
        self.last_aruco_corners = corners
        self.last_aruco_ids = marker_ids
        return metadata, frame, _centers_from_corners(corners)


def main(
    device_sn: str,
    endpoint: str,
    receive_timeout_ms: Optional[int],
    receive_hwm: int,
    show: bool,
    aruco_dictionary_name: str = "DICT_4X4_50",
) -> int:
    if not device_sn:
        logger.error("device_sn 不能为空")
        return 1

    try:
        with ZmqVideoSubscriber(
            device_sn=device_sn,
            endpoint=endpoint,
            receive_timeout_ms=receive_timeout_ms,
            receive_hwm=receive_hwm,
        ) as subscriber:
            while True:
                metadata, frame, centers = subscriber.receive_with_aruco(
                    aruco_dictionary_name
                )
                stream_type = metadata["stream_type"]
                logger.info(
                    "stream=%s frame_id=%s shape=%s rows=%d columns=%d",
                    stream_type,
                    metadata["frame_id"],
                    frame.shape,
                    frame.shape[0],
                    frame.shape[1],
                )
                if len(subscriber.stream_shapes) > 1:
                    logger.info(
                        "latest stream shapes: color=%s depth=%s",
                        subscriber.stream_shapes.get("color"),
                        subscriber.stream_shapes.get("depth"),
                    )
                if stream_type == "color":
                    logger.info("aruco_centers=%s", centers)
                if show:
                    if stream_type == "depth":
                        depth_mm = (
                            frame.astype(np.float32) * metadata["depth_scale"]
                        )
                        preview = cv2.convertScaleAbs(
                            depth_mm, alpha=255.0 / 4000
                        )
                        preview = cv2.applyColorMap(preview, cv2.COLORMAP_JET)
                    else:
                        preview = draw_aruco_annotations(
                            frame,
                            subscriber.last_aruco_corners,
                            subscriber.last_aruco_ids,
                            centers,
                        )
                    cv2.imshow(f"{device_sn}/{stream_type}", preview)
                    if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                        break
    except KeyboardInterrupt:
        pass
    except TimeoutError as exc:
        logger.error("%s", exc)
        return 1
    finally:
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    device_sn = "AY68B520047"
    endpoint = "tcp://10.20.2.171:5559"
    receive_timeout_ms = 5000
    receive_hwm = 2
    show = True
    aruco_dictionary_name = "DICT_4X4_50"
    raise SystemExit(
        main(
            device_sn,
            endpoint,
            receive_timeout_ms,
            receive_hwm,
            show,
            aruco_dictionary_name,
        )
    )
