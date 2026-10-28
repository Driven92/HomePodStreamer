# -*- coding: utf-8 -*-
"""把 homepod_streamer 打包成 Windows 程序，并自动生成发布压缩包。

用法：
    python build_exe.py                # 打包 + 压缩
    python build_exe.py --no-zip       # 只打包，不压缩
    python build_exe.py --no-clean     # 复用上次的编译缓存（更快，但可能残留旧文件）

产物：
    build/pkg/v<版本>/HomePodStreamer/HomePodStreamer.exe   ← 单目录形态，直接双击运行
    dist/HomePodStreamer-v<版本>-win64.zip                  ← 整个文件夹的压缩包（发布用）

输出目录按版本命名、永不覆盖（本机安全策略禁止批量删除，覆盖旧目录会被中断）。

为什么改成**单目录（--onedir）而不是单文件（--onefile）**：
- 单文件 exe 每次启动都要把 ~160 MB 内容解压到 %TEMP%/\\_MEIxxxxxx，慢、且吃 C 盘；
  异常退出还会留下 156 MB 垃圾目录（攒几次就是 GB 级）。
- 单目录形态原地加载 DLL，**启动秒开、不写临时目录、不占额外空间**。
- 代价：不能只拷一个 exe，要连同同级的 _internal 目录一起拷（所以打成 zip 分发）。

版本号只在 homepod_streamer/version.py 里维护，本脚本自动读取，用于：
- 命名压缩包；
- 写 exe 的 Windows 版本资源（资源管理器“属性 → 详细信息”里能看到）。
"""
import argparse
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent
PKG = ROOT / "homepod_streamer"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
PKG_ROOT = BUILD / "pkg"        # 每次打包的产物目录（按版本命名，互不覆盖）
APP_NAME = "HomePodStreamer"

# 明确不用的 PyQt6 子模块（体积大头）
_EXCLUDE_QT = [
    "PyQt6.QtQml",
    "PyQt6.QtQuick",
    "PyQt6.QtQuick3D",
    "PyQt6.QtQuickWidgets",
    "PyQt6.QtQuickControls2",
    "PyQt6.QtMultimedia",
    "PyQt6.QtMultimediaWidgets",
    "PyQt6.QtPdf",
    "PyQt6.QtPdfWidgets",
    "PyQt6.QtDesigner",
    "PyQt6.QtOpenGL",
    "PyQt6.QtOpenGLWidgets",
    "PyQt6.QtNetwork",
    "PyQt6.QtSql",
    "PyQt6.QtTest",
    "PyQt6.QtDBus",
    "PyQt6.QtSvg",
    "PyQt6.QtSvgWidgets",
]

# 本程序实际用到的（QSystemTrayIcon / QPainter / QSlider 等都在 QtWidgets）
_HIDDEN_QT = [
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
]


def read_version() -> str:
    """从 version.py 解析 __version__（不导入包，避免触发 soundcard/pyatv 初始化）。"""
    src = (PKG / "version.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', src, re.M)
    if not m:
        raise SystemExit("无法从 homepod_streamer/version.py 解析 __version__")
    return m.group(1)


def write_version_file(version: str, path: Path) -> Path:
    """生成 PyInstaller 的 Windows 版本资源文件（中文语言 0x0804 / 编码 1200）。"""
    try:
        major, minor, patch = (int(x) for x in version.split(".")[:3])
    except ValueError:
        major, minor, patch = 0, 0, 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""# UTF-8
# 由 build_exe.py 自动生成，请勿手工修改
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, 0),
    prodvers=({major}, {minor}, {patch}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [StringTable('080404B0', [
        StringStruct('CompanyName', 'HomePod Streamer'),
        StringStruct('FileDescription', 'HomePod Streamer - 把 Windows 系统声音投到 HomePod'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', '{APP_NAME}'),
        StringStruct('OriginalFilename', '{APP_NAME}.exe'),
        StringStruct('ProductName', 'HomePod Streamer'),
        StringStruct('ProductVersion', '{version}'),
        StringStruct('LegalCopyright', ''),
      ])]
    ),
    VarFileInfo([VarStruct('Translation', [0x0804, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    return path


def build(version: str, clean: bool) -> Path:
    """调用 PyInstaller 打包成单目录形态，返回 exe 路径。

    **每次输出到以版本命名的全新目录**，从不覆盖旧目录。原因很实际：本机安全策略
    禁止单次批量删除超过 50 个文件，而 PyInstaller 清理非空输出目录时要删一千多个，
    会在 COLLECT 阶段直接中断打包（`Removing dir ...` 之后就没了）。改成写新目录，
    流程里就不存在删除动作，反复打包也不会被拦。
    """
    ver_file = write_version_file(version, BUILD / "version_info.txt")

    stage = PKG_ROOT / f"v{version}"
    if stage.exists():                      # 同一版本重复打包：加时间戳，仍不覆盖
        stage = PKG_ROOT / f"v{version}-{time.strftime('%H%M%S')}"
    stage.mkdir(parents=True, exist_ok=True)

    args = [
        "--onedir",
        "--noconsole",
        "--name",
        APP_NAME,
        "--distpath",
        str(stage),
        "--workpath",
        str(BUILD),
        "--specpath",
        str(ROOT),
        "--version-file",
        str(ver_file),
        "--hidden-import",
        "homepod_streamer",
        "--hidden-import",
        "logging.handlers",
        "--collect-all",
        "pyatv",
        "--collect-all",
        "soundcard",
        "--collect-all",
        "numpy",
        "--collect-all",
        "miniaudio",
        "--collect-all",
        "cryptography",
    ]
    if clean:
        args.insert(0, "--clean")

    # 应用图标：既作为 exe 的文件图标，也随包分发供窗口/托盘运行时加载
    ico = ROOT / "assets" / "icon.ico"
    if ico.is_file():
        args += ["--icon", str(ico), "--add-data", f"{ico};assets"]
    else:
        print("[warn] 未找到 assets/icon.ico，本次打包不带图标（可跑 tools/make_icon.py 生成）")

    for mod in _HIDDEN_QT:
        args += ["--hidden-import", mod]
    for mod in _EXCLUDE_QT:
        args += ["--exclude-module", mod]

    args.append(str(PKG / "__main__.py"))
    PyInstaller.__main__.run(args)

    exe = stage / APP_NAME / f"{APP_NAME}.exe"
    if not exe.is_file():
        raise SystemExit(f"打包失败：未生成 {exe}")
    return exe


def make_zip(version: str, src: Path) -> Path:
    """把产物目录整个压成发布包（内含使用说明）。"""
    DIST.mkdir(parents=True, exist_ok=True)
    zip_path = DIST / f"{APP_NAME}-v{version}-win64.zip"
    if zip_path.exists():
        zip_path.unlink()

    readme = PKG / "README.md"
    t0 = time.time()
    with zipfile.ZipFile(
        zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as z:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                z.write(p, f"{APP_NAME}/{p.relative_to(src).as_posix()}")
        if readme.is_file():
            z.write(readme, f"{APP_NAME}/使用说明.md")
    print(f"已生成压缩包：{zip_path.name}（耗时 {time.time() - t0:.0f}s）")
    return zip_path


def _dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def main() -> None:
    ap = argparse.ArgumentParser(description="打包 HomePod Streamer")
    ap.add_argument("--no-zip", action="store_true", help="只打包，不生成压缩包")
    ap.add_argument("--no-clean", action="store_true", help="复用编译缓存")
    ns = ap.parse_args()

    version = read_version()
    print(f"=== HomePod Streamer v{version} 打包开始（单目录模式）===")

    exe = build(version, clean=not ns.no_clean)
    folder = exe.parent
    print(f"exe  ：{exe}")
    print(f"目录 ：{folder}（{_dir_size(folder) / 1024 / 1024:.0f} MB）")

    if not ns.no_zip:
        z = make_zip(version, folder)
        print(f"压缩包：{z}（{z.stat().st_size / 1024 / 1024:.1f} MB）")

    print("=== 完成 ===")
    print("提示：运行请用整个文件夹（exe 与 _internal 目录必须在一起），不要只拷 exe。")
    print(f"提示：历次产物在 {PKG_ROOT}，空间紧张时可整个删掉。")


if __name__ == "__main__":
    sys.exit(main())
