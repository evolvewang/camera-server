from pathlib import Path
from typing import Any

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "configs" / "config.yaml"


def load_config() -> dict[str, Any]:
    """Load the repository-wide YAML configuration."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file)
    except yaml.YAMLError as exc:
        raise RuntimeError(
            f"Failed to parse yaml config file: {CONFIG_PATH}"
        ) from exc
    if not isinstance(config, dict):
        raise RuntimeError(f"Config file must contain a mapping: {CONFIG_PATH}")
    return config


app_config = load_config()  # 全局配置命名 app_config

if __name__ == "__main__":
    print(app_config)
