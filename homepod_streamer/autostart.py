# -*- coding: utf-8 -*-
"""Windows 开机自启管理（写入当前用户 Run 注册表，无需管理员权限）。

自启命令固定为 ``<本程序> --minimized``：
- 打包成 exe 后，``sys.executable`` 即 exe 自身；
- 以脚本方式运行时，退化为 ``python -m homepod_streamer --minimized``。
``--minimized`` 会让程序启动后隐藏主窗口、常驻系统托盘，并自动连接上次使用的设备。
"""
import sys

import winreg

APP_NAME = "HomePodStreamer"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _command_string() -> str:
    """返回写入注册表的自启命令（路径带空格时会加引号）。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    # 脚本模式：用当前解释器运行模块入口
    return f'"{sys.executable}" -m homepod_streamer --minimized'


def is_enabled() -> bool:
    """当前是否已启用开机自启。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, APP_NAME)
        return bool(val)
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    """启用/禁用开机自启。"""
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as k:
        if enabled:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _command_string())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass
