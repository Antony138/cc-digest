#!/usr/bin/env python3
"""生成 PWA 图标：深青绿圆角底 + 白色「>」提示符与三条变更线（终端 + changelog）。只需运行一次。"""
from pathlib import Path

from PIL import Image, ImageDraw

DOCS = Path(__file__).resolve().parent.parent / "docs"
BG = (15, 118, 110)     # #0F766E 深青绿
FG = (255, 255, 255)

S = 2  # 2x 超采样，斜线更平滑
size = 512 * S

img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([0, 0, size - 1, size - 1], radius=116 * S, fill=BG)

# 「>」提示符：折线 + 圆头端点
W = 48 * S
pts = [(136 * S, 178 * S), (222 * S, 256 * S), (136 * S, 334 * S)]
d.line(pts, fill=FG, width=W, joint="curve")
for x, y in pts:
    d.ellipse([x - W // 2, y - W // 2, x + W // 2, y + W // 2], fill=FG)

# 三条「变更记录」脉冲线，长短错落
H = 44 * S
for x0, yc, ln in ((284, 178, 104), (284, 256, 130), (284, 334, 82)):
    x0, yc, ln = x0 * S, yc * S, ln * S
    d.rounded_rectangle([x0, yc - H // 2, x0 + ln, yc + H // 2], radius=H // 2, fill=FG)

DOCS.mkdir(parents=True, exist_ok=True)
img512 = img.resize((512, 512), Image.LANCZOS)
img512.save(DOCS / "icon-512.png")
img512.resize((192, 192), Image.LANCZOS).save(DOCS / "icon-192.png")

flat = Image.new("RGB", (512, 512), BG)  # apple-touch-icon 不要透明角
flat.paste(img512, (0, 0), img512)
flat.resize((180, 180), Image.LANCZOS).save(DOCS / "apple-touch-icon.png")
print("icons written")
