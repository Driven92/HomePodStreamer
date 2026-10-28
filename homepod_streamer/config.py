# -*- coding: utf-8 -*-
"""极简的用户配置持久化（JSON，存于 %APPDATA%/HomePodStreamer）。

保存少量偏好：上次连接的设备名、音量、是否开机自启。读写失败均静默降级，
绝不因配置问题阻塞主流程。
"""
import json
import os

APP_DIR_NAME = "HomePodStreamer"


def config_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_DIR_NAME)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return os.path.join(d, "config.json")


def load_config() -> dict:
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def save_config(data: dict) -> None:
    try:
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def update(**kwargs) -> dict:
    cfg = load_config()
    cfg.update(kwargs)
    save_config(cfg)
    return cfg
