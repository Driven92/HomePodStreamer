# -*- coding: utf-8 -*-
"""生成应用图标：assets/icon.ico（多尺寸）、assets/icon.png（512px 预览）。

设计：强调色渐变方角圆块（与应用主色一致）+ 白色 HomePod 胶囊剪影 + 圆点网罩 + 两侧声波。
小尺寸（<=24px）自动降级为简化画法（去掉网罩与声波、加粗轮廓），避免缩略后糊成一团。

用法：
    python tools/make_icon.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets"

BG_1 = (79, 125, 255)      # #4F7DFF
BG_2 = (166, 107, 255)     # #A66BFF
PILL = (255, 255, 255, 255)
DOT = (58, 92, 190, 105)   # 网罩上的点，半透明深蓝
WAVE = (255, 255, 255, 210)

SS = 4                     # 超采样倍数（先画 4 倍大再缩小，等价于抗锯齿）


def _gradient(size: int) -> Image.Image:
    """对角线线性渐变（左上 BG_1 → 右下 BG_2）+ 左上柔和高光。"""
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    t = (x + y) / (2.0 * (size - 1))
    rgb = np.stack([
        BG_1[0] + (BG_2[0] - BG_1[0]) * t,
        BG_1[1] + (BG_2[1] - BG_1[1]) * t,
        BG_1[2] + (BG_2[2] - BG_1[2]) * t,
    ], axis=-1)

    # 左上角柔光：让纯色块有一点光源感
    d = np.sqrt((x - size * 0.26) ** 2 + (y - size * 0.20) ** 2) / (size * 0.95)
    glow = np.clip(1.0 - d, 0.0, 1.0) ** 2 * 58.0
    rgb = rgb + (255.0 - rgb) * (glow / 255.0)[..., None]

    return Image.fromarray(rgb.round().clip(0, 255).astype(np.uint8), "RGB").convert("RGBA")


def _squircle_mask(size: int, radius_ratio: float = 0.225) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    r = size * radius_ratio
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=r, fill=255)
    return m


def _draw_waves(d: ImageDraw.ImageDraw, S: int, cx: float, cy: float,
                pill_w: float) -> None:
    """胶囊两侧的声波弧线（左右对称，各两道）。"""
    width = max(1, int(S * 0.028))
    for side in (-1, 1):
        for frac in (0.285, 0.372):
            rr = S * frac
            box = (cx - rr, cy - rr, cx + rr, cy + rr)
            # 以胶囊中心为圆心，在左右两侧各取一段弧
            mid = 0 if side < 0 else 180
            d.arc(box, start=mid - 28, end=mid + 28, fill=WAVE, width=width)


def draw_icon(size: int) -> Image.Image:
    """按目标尺寸直接绘制（不是缩放大图），保证小图标清晰。"""
    S = size * SS
    base = _gradient(S)

    # 分三档画法：小尺寸只留剪影，中等尺寸加声波，大尺寸才上网罩圆点
    mesh = size >= 64            # 网罩圆点
    waves = size >= 32           # 声波弧线
    pill_w = S * (0.355 if mesh else (0.38 if waves else 0.46))
    pill_h = S * (0.545 if mesh else (0.57 if waves else 0.62))
    cx, cy = S / 2.0, S / 2.0

    # 声波在底层：先画，让胶囊压住根部
    if waves:
        _draw_waves(ImageDraw.Draw(base), S, cx, cy, pill_w)

    # 白色胶囊（顶部纯白、底部略带冷调，避免看起来像镂空的洞）
    box = (cx - pill_w / 2, cy - pill_h / 2, cx + pill_w / 2, cy + pill_h / 2)
    solid = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(solid).rounded_rectangle(box, radius=pill_w / 2, fill=PILL)
    shade = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    top, bot = int(cy - pill_h / 2), int(cy + pill_h / 2)
    for yy in range(max(0, top), min(S, bot + 1)):
        k = (yy - top) / max(1, bot - top)
        c = (int(255 - 16 * k), int(255 - 12 * k), 255, 255)
        sd.line((0, yy, S, yy), fill=c)
    pill = Image.composite(shade, Image.new("RGBA", (S, S), (0, 0, 0, 0)),
                           solid.getchannel("A"))
    pill.putalpha(solid.getchannel("A"))

    # 网罩圆点（六边形错排，模拟 HomePod 的织物网面）
    if mesh:
        dot_r = max(1, int(S * 0.0105))
        step = S * 0.052
        rows = int(pill_h / step)
        cols = int(pill_w / step) + 1
        dots = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        dd = ImageDraw.Draw(dots)
        for row in range(rows):
            y = cy - pill_h / 2 + S * 0.055 + row * step
            offset = (step / 2) if row % 2 else 0
            for col in range(cols):
                x = cx - pill_w / 2 + S * 0.035 + col * step + offset
                if abs(x - cx) < pill_w / 2 - dot_r * 1.2:
                    dd.ellipse((x - dot_r, y - dot_r, x + dot_r, y + dot_r), fill=DOT)
        # 只保留落在胶囊内的点
        pill_mask = Image.new("L", (S, S), 0)
        ImageDraw.Draw(pill_mask).rounded_rectangle(box, radius=pill_w / 2, fill=255)
        dots.putalpha(Image.composite(dots.getchannel("A"),
                                      Image.new("L", (S, S), 0), pill_mask))
        pill = Image.alpha_composite(pill, dots)

    base = Image.alpha_composite(base, pill)

    # 圆角裁切
    base.putalpha(_squircle_mask(S))
    return base.resize((size, size), Image.LANCZOS)


def _dib_frame(im: Image.Image) -> bytes:
    """把一帧转成 ICO 用的 BMP/DIB 数据（32bpp BGRA，自下而上 + AND 掩码）。

    **为什么必须有 DIB 帧**：Windows 的经典图标加载器（`ImageList_ReplaceIcon`、
    资源管理器列表视图、任务栏、文件对话框）只认 BMP 帧；PNG 压缩帧官方仅支持
    256×256 这一档。整包都是 PNG 时，小尺寸图标会加载失败，Explorer 直接退化成
    通用文件图标（表现为"exe 图标没生效"，但 exe 里其实有图标资源）。
    """
    arr = np.array(im.convert("RGBA"), dtype=np.uint8)      # 上到下
    flipped = arr[::-1]                                     # ICO 是自下而上存
    bgra = np.ascontiguousarray(flipped[:, :, [2, 1, 0, 3]]).tobytes()

    # AND 掩码：1 表示透明（与 alpha=0 对应），每行按 4 字节对齐
    bits = np.packbits(flipped[:, :, 3] == 0, axis=1)       # (h, ceil(w/8))
    pad = (-bits.shape[1]) % 4
    if pad:
        bits = np.pad(bits, ((0, 0), (0, pad)))
    mask = np.ascontiguousarray(bits).tobytes()

    h, w = arr.shape[:2]
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    return header + bgra + mask


def write_ico(path: Path, images: list[Image.Image]) -> None:
    """手写 ICO，每帧用各自绘制好的位图（不做 PIL 的统一缩放，避免糊边）。

    帧格式按尺寸区分：
    - 256×256 → PNG（体积从 256 KB 降到 ~26 KB，Vista 起官方支持）
    - 其余尺寸 → BMP/DIB（Windows 各壳层都能加载，小图标才显示得出来）
    """
    blobs = []
    for im in images:
        if im.width >= 256:
            import io
            buf = io.BytesIO()
            im.save(buf, format="PNG", optimize=True)
            blobs.append((im.width, im.height, buf.getvalue()))
        else:
            blobs.append((im.width, im.height, _dib_frame(im)))

    n = len(blobs)
    header = struct.pack("<HHH", 0, 1, n)
    offset = 6 + 16 * n
    entries, data = b"", b""
    for w, h, blob in blobs:
        entries += struct.pack("<BBBBHHII",
                              w % 256, h % 256, 0, 0, 1, 32,
                              len(blob), offset + len(data))
        data += blob
    path.write_bytes(header + entries + data)


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    sizes = [256, 128, 96, 64, 48, 40, 32, 24, 20, 16]
    images = [draw_icon(s) for s in sizes]

    ico = OUT_DIR / "icon.ico"
    write_ico(ico, images)
    png = OUT_DIR / "icon.png"
    images[1].save(png)                    # 128px 预览
    draw_icon(256).save(OUT_DIR / "icon-256.png")

    print(f"ico : {ico}  ({ico.stat().st_size / 1024:.1f} KB, {len(sizes)} 个尺寸)")
    print(f"png : {png}")
    print(f"尺寸 : {sizes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
