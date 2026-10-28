# -*- coding: utf-8 -*-
"""HomePod Streamer —— 把 Windows 系统声音投到 HomePod 的小工具。

替代已停更、被新固件淘汰的 TuneBlade / AirParrot：
底层用仍在维护的 pyatv 完成 AirPlay/RAOP 握手（已验证可连 HomePod 固件 27.0），
音频抓取用 soundcard 的 WASAPI 环回（自动重采样到 44.1kHz，与 RAOP 对齐）。

图形界面见 ui.py（PyQt6 自绘深色面板），本文件只负责命令行入口与日志初始化。

用法：
  python -m homepod_streamer               启动图形界面（默认）
  python -m homepod_streamer --scan        仅列出局域网内可投的设备
  python -m homepod_streamer --name 客厅    直接投到名为“客厅”的设备
  python -m homepod_streamer --test         向设备播放一段提示音（有声），验证投送链路
  python -m homepod_streamer --autostart on|off   设置开机自启并退出
  python -m homepod_streamer --minimized    隐藏窗口、常驻托盘并自动连接上次设备
  python -m homepod_streamer --name 客厅 --latency 0.4   指定输出缓冲（秒）
  python -m homepod_streamer --version      打印版本号

延迟说明：整个链路的延迟 ≈ 输出缓冲(latency) + 音箱解码渲染(约 70ms) + 网络。
pyatv 默认缓冲 1.5 秒，本程序默认压到 0.5 秒；局域网稳定时 0.4 甚至 0.25 也可用，
若出现断续就把缓冲调大一档。
"""
import argparse
import logging
import os
import sys

from homepod_streamer import (
    HomePodStreamer,
    WasapiLoopbackCapture,
    tone_generator,
)
from homepod_streamer import autostart, config
from homepod_streamer.streamer import (
    DEFAULT_LATENCY_S,
    configure_latency,
    current_latency,
)
from homepod_streamer.version import version_string


def _setup_logging():
    # 打包成 exe（--noconsole）后没有控制台，把关键日志落盘便于排查。
    if not getattr(sys, "frozen", False):
        return
    try:
        from logging.handlers import RotatingFileHandler

        log_dir = os.path.join(os.environ.get("APPDATA", "."), "HomePodStreamer")
        os.makedirs(log_dir, exist_ok=True)
        # 必须显式指定 UTF-8：默认按系统本地编码（中文 Windows 是 GBK）写，
        # 日志里的中文会变成乱码，排查时几乎没法读。
        # 同时限制单个文件大小，避免长跑后无限膨胀。
        handler = RotatingFileHandler(
            os.path.join(log_dir, "app.log"),
            maxBytes=1_000_000,
            backupCount=2,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.addHandler(handler)
    except OSError:
        pass


def cmd_scan(args):
    s = HomePodStreamer()
    devs = s.scan(timeout=args.timeout)
    if not devs:
        print("未发现任何 AirPlay/RAOP 设备。")
    for d in devs:
        print(f"  {d['name']}  @  {d['address']}")
    s.close()


def cmd_stream(args, test=False):
    s = HomePodStreamer(on_error=lambda msg: print(f"[错误] {msg}"))
    devs = s.scan(timeout=args.timeout)
    if not devs:
        print("未发现设备，退出。")
        s.close()
        return

    target = None
    if args.name:
        for d in devs:
            if args.name in d["name"]:
                target = d
                break
    elif args.ip:
        for d in devs:
            if d["address"] == args.ip:
                target = d
                break
    else:
        target = devs[0]
    if target is None:
        print(f"未匹配到设备（name={args.name} ip={args.ip}）")
        s.close()
        return

    print(f"开始投流到：{target['name']} @ {target['address']}")
    print(f"输出缓冲：{current_latency():g}s")

    if test:
        gen = tone_generator()
        capture = None
    else:
        capture = WasapiLoopbackCapture()
        gen = capture.generator()

    s.start_stream(target["conf"], gen, capture=capture)
    print("正在推流…… 按 Ctrl+C 停止。")
    try:
        import time

        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        s.close()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _setup_logging()

    parser = argparse.ArgumentParser(description="把 Windows 系统声音投到 HomePod")
    parser.add_argument("--scan", action="store_true", help="列出设备后退出")
    parser.add_argument("--name", help="按名称匹配设备")
    parser.add_argument("--ip", help="按 IP 匹配设备")
    parser.add_argument("--test", action="store_true",
                        help="播放一段有声测试音，验证投送链路")
    parser.add_argument("--timeout", type=int, default=5)
    parser.add_argument("--autostart", choices=["on", "off"], default=None,
                        help="设置开机自启并退出（on/off）")
    parser.add_argument("--minimized", action="store_true",
                        help="启动后隐藏窗口并常驻托盘（配合开机自启）")
    parser.add_argument("--latency", type=float, default=None,
                        help="输出缓冲秒数（0.25~1.5），越小延迟越低，默认 0.5")
    parser.add_argument("--version", action="version",
                        version=f"HomePod Streamer {version_string()}")
    args = parser.parse_args()

    # 延迟设置：命令行优先，其次读配置，最后用默认值
    saved_latency = config.load_config().get("latency", DEFAULT_LATENCY_S)
    configure_latency(args.latency if args.latency is not None else saved_latency)

    if args.autostart is not None:
        want = args.autostart == "on"
        autostart.set_enabled(want)
        config.update(autostart=want)
        print(f"开机自启已{'启用' if want else '禁用'}")
        return

    if args.scan:
        cmd_scan(args)
    elif args.name or args.ip or args.test:
        cmd_stream(args, test=args.test)
    else:
        # 延迟导入：命令行用法不需要拉起 Qt
        from homepod_streamer.ui import run_gui

        run_gui(minimized=args.minimized)


if __name__ == "__main__":
    main()
