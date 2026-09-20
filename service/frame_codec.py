"""Shared wire encoding for published and requested video frames."""

import math
from typing import Any, Protocol

import cv2
import numpy as np

from service.video_protocol import DEPTH_ENCODING, JPEG_ENCODING, PROTOCOL_VERSION


class FramePacketLike(Protocol):
    frame_id: int
    timestamp: float
    frame: np.ndarray
    depth_scale: float | None


class FrameEncodingError(ValueError):
    """OpenCV could not encode an otherwise valid frame."""


def encode_frame(
    device_sn: str,
    stream_type: str,
    packet: FramePacketLike,
    jpeg_quality: int,
) -> tuple[dict[str, Any], bytes]:
    """Encode a camera packet using the existing video frame contract."""
    frame = packet.frame
    if stream_type == "color":
        if frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError("color 帧必须为三通道 uint8 BGR")
        success, encoded = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
        )
        encoding, channels = JPEG_ENCODING, 3
    elif stream_type == "depth":
        if frame.dtype != np.uint16 or frame.ndim != 2:
            raise ValueError("depth 帧必须为单通道 uint16")
        scale = packet.depth_scale
        if (
            isinstance(scale, bool)
            or not isinstance(scale, (int, float))
            or not math.isfinite(scale)
            or scale <= 0
        ):
            raise ValueError("depth_scale 必须为正的有限数")
        success, encoded = cv2.imencode(".png", frame)
        encoding, channels = DEPTH_ENCODING, 1
    else:
        raise ValueError(f"不支持的视频流: {stream_type}")

    if not success:
        raise FrameEncodingError(f"{stream_type} 帧编码失败")

    metadata = {
        "protocol_version": PROTOCOL_VERSION,
        "device_sn": device_sn,
        "stream_type": stream_type,
        "frame_id": packet.frame_id,
        "timestamp": packet.timestamp,
        "width": frame.shape[1],
        "height": frame.shape[0],
        "channels": channels,
        "encoding": encoding,
    }
    if stream_type == "depth":
        metadata["depth_scale"] = packet.depth_scale
    return metadata, encoded.tobytes()


def decode_frame(
    metadata: object,
    image_bytes: bytes,
    device_sn: str,
) -> np.ndarray:
    """Validate and decode one v2 color or depth frame."""
    if not isinstance(metadata, dict):
        raise ValueError("视频 metadata 必须是 JSON 对象")
    if type(metadata.get("protocol_version")) is not int or (
        metadata["protocol_version"] != PROTOCOL_VERSION
    ):
        raise ValueError(f"不支持的协议版本: {metadata.get('protocol_version')}")
    if metadata.get("device_sn") != device_sn:
        raise ValueError("metadata.device_sn 与相机序列号不一致")

    stream_type = metadata.get("stream_type")
    encoding = metadata.get("encoding")
    if not isinstance(stream_type, str):
        raise ValueError("metadata.stream_type 必须是字符串")
    expected_encoding = {
        "color": JPEG_ENCODING,
        "depth": DEPTH_ENCODING,
    }.get(stream_type)
    if expected_encoding is None or encoding != expected_encoding:
        raise ValueError(f"不支持的视频流或编码: {stream_type}/{encoding}")

    for field in ("width", "height", "channels"):
        value = metadata.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"metadata.{field} 必须是正整数")
    frame_id = metadata.get("frame_id")
    if isinstance(frame_id, bool) or not isinstance(frame_id, int) or frame_id < 0:
        raise ValueError("metadata.frame_id 必须是非负整数")
    timestamp = metadata.get("timestamp")
    if (
        isinstance(timestamp, bool)
        or not isinstance(timestamp, (int, float))
        or not math.isfinite(timestamp)
    ):
        raise ValueError("metadata.timestamp 必须是有限数字")
    if stream_type == "depth":
        scale = metadata.get("depth_scale")
        if (
            isinstance(scale, bool)
            or not isinstance(scale, (int, float))
            or not math.isfinite(scale)
            or scale <= 0
        ):
            raise ValueError("无效 depth_scale")

    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    decode_mode = cv2.IMREAD_COLOR if stream_type == "color" else cv2.IMREAD_UNCHANGED
    if not image_bytes:
        raise ValueError(f"{stream_type} 视频帧数据为空")
    try:
        frame = cv2.imdecode(image_array, decode_mode)
    except cv2.error as exc:
        raise ValueError(f"{stream_type} 视频帧解码失败") from exc
    if frame is None:
        raise ValueError(f"{stream_type} 视频帧解码失败")

    expected_channels = 3 if stream_type == "color" else 1
    if (
        frame.shape[:2] != (metadata["height"], metadata["width"])
        or metadata["channels"] != expected_channels
    ):
        raise ValueError("图像尺寸或通道数与元数据不一致")
    if stream_type == "color" and (
        frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3
    ):
        raise ValueError("彩色图必须为三通道 uint8 BGR")
    if stream_type == "depth" and (frame.dtype != np.uint16 or frame.ndim != 2):
        raise ValueError("深度图必须为单通道 uint16")
    return frame
