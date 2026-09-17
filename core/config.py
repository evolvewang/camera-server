"""Load and validate the global project configuration."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "configs" / "config.yaml"


class ConfigError(ValueError):
    """Raised when the project configuration is missing or invalid."""


@dataclass(frozen=True)
class ProjectSettings:
    name: str


@dataclass(frozen=True)
class CameraSettings:
    device_sn: str


@dataclass(frozen=True)
class StreamSettings:
    jpeg_quality: int


@dataclass(frozen=True)
class ZmqSettings:
    publisher_endpoint: str
    subscriber_endpoint: str
    send_hwm: int
    receive_hwm: int
    receive_timeout_ms: int


@dataclass(frozen=True)
class ClientSettings:
    show: bool


@dataclass(frozen=True)
class LoggingSettings:
    level: str
    directory: str
    filename: str
    backup_count: int


@dataclass(frozen=True)
class AppConfig:
    project: ProjectSettings
    camera: CameraSettings
    stream: StreamSettings
    zmq: ZmqSettings
    client: ClientSettings
    logging: LoggingSettings


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"配置项 {name} 必须是映射")
    return value


def _reject_unknown_keys(
    mapping: dict[str, Any], allowed_keys: set[str], name: str
) -> None:
    unknown_keys = set(mapping) - allowed_keys
    if unknown_keys:
        raise ConfigError(f"配置项 {name} 包含未知字段: {sorted(unknown_keys)}")


def _require_string(mapping: dict[str, Any], key: str, section: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise ConfigError(f"配置项 {section}.{key} 必须是字符串")
    return value.strip()


def _require_non_empty_string(
    mapping: dict[str, Any], key: str, section: str
) -> str:
    value = _require_string(mapping, key, section)
    if not value:
        raise ConfigError(f"配置项 {section}.{key} 不能为空")
    return value


def _require_positive_int(mapping: dict[str, Any], key: str, section: str) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"配置项 {section}.{key} 必须是正整数")
    return value


def _require_bool(mapping: dict[str, Any], key: str, section: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise ConfigError(f"配置项 {section}.{key} 必须是布尔值")
    return value


def load_config(config_path: str | Path = CONFIG_PATH) -> AppConfig:
    """Read and validate a Camera Server YAML configuration file."""
    path = Path(config_path)
    try:
        raw_config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"配置文件不存在: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"无法读取配置文件: {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件 YAML 格式错误: {path}: {exc}") from exc

    root = _require_mapping(raw_config, "root")
    root_keys = {"project", "camera", "stream", "zmq", "client", "logging"}
    _reject_unknown_keys(root, root_keys, "root")
    missing_sections = root_keys - set(root)
    if missing_sections:
        raise ConfigError(f"配置文件缺少分组: {sorted(missing_sections)}")

    project = _require_mapping(root["project"], "project")
    camera = _require_mapping(root["camera"], "camera")
    stream = _require_mapping(root["stream"], "stream")
    zmq_settings = _require_mapping(root["zmq"], "zmq")
    client = _require_mapping(root["client"], "client")
    logging_settings = _require_mapping(root["logging"], "logging")

    _reject_unknown_keys(project, {"name"}, "project")
    _reject_unknown_keys(camera, {"device_sn"}, "camera")
    _reject_unknown_keys(stream, {"jpeg_quality"}, "stream")
    _reject_unknown_keys(
        zmq_settings,
        {
            "publisher_endpoint",
            "subscriber_endpoint",
            "send_hwm",
            "receive_hwm",
            "receive_timeout_ms",
        },
        "zmq",
    )
    _reject_unknown_keys(client, {"show"}, "client")
    _reject_unknown_keys(
        logging_settings,
        {"level", "directory", "filename", "backup_count"},
        "logging",
    )

    jpeg_quality = _require_positive_int(stream, "jpeg_quality", "stream")
    if jpeg_quality > 100:
        raise ConfigError("配置项 stream.jpeg_quality 必须在 1 到 100 之间")

    log_level = _require_non_empty_string(
        logging_settings, "level", "logging"
    ).upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError(f"不支持的日志级别: {log_level}")

    return AppConfig(
        project=ProjectSettings(
            name=_require_non_empty_string(project, "name", "project")
        ),
        camera=CameraSettings(
            device_sn=_require_string(camera, "device_sn", "camera")
        ),
        stream=StreamSettings(jpeg_quality=jpeg_quality),
        zmq=ZmqSettings(
            publisher_endpoint=_require_non_empty_string(
                zmq_settings, "publisher_endpoint", "zmq"
            ),
            subscriber_endpoint=_require_non_empty_string(
                zmq_settings, "subscriber_endpoint", "zmq"
            ),
            send_hwm=_require_positive_int(zmq_settings, "send_hwm", "zmq"),
            receive_hwm=_require_positive_int(
                zmq_settings, "receive_hwm", "zmq"
            ),
            receive_timeout_ms=_require_positive_int(
                zmq_settings, "receive_timeout_ms", "zmq"
            ),
        ),
        client=ClientSettings(show=_require_bool(client, "show", "client")),
        logging=LoggingSettings(
            level=log_level,
            directory=_require_non_empty_string(
                logging_settings, "directory", "logging"
            ),
            filename=_require_non_empty_string(
                logging_settings, "filename", "logging"
            ),
            backup_count=_require_positive_int(
                logging_settings, "backup_count", "logging"
            ),
        ),
    )


app_config = load_config()


if __name__ == "__main__":
    print(app_config)
