import math
from PIL import Image, ImageDraw

W, H = 800, 800
img = Image.new("RGB", (W, H), "#090d16")
d = ImageDraw.Draw(img)

# 1. 背景光暈與科技感上升折線圖
for r in range(360, 0, -10):
    c = int(13 + (360 - r) * 0.08)
    d.ellipse((400 - r, 380 - r, 400 + r, 380 + r), fill=(15, c, min(255, c + 35)))

chart_pts = [(40, 560), (160, 500), (260, 530), (380, 410), (490, 440), (620, 290), (760, 210)]
d.line(chart_pts, fill="#38bdf8", width=10)
for px, py in chart_pts[1:-1]:
    d.ellipse((px - 10, py - 10, px + 10, py + 10), fill="#090d16", outline="#38bdf8", width=4)

# 2. 中央發光金色比特幣徽章 (BTC)
bx, by, br = 400, 240, 82
d.ellipse((bx - br - 10, by - br - 10, bx + br + 10, by + br + 10), outline="#fbbf24", width=3)
d.ellipse((bx - br, by - br, bx + br, by + br), fill="#f59e0b", outline="#fef08a", width=6)
# 繪製標準比特幣 B 符號
d.rectangle((bx - 24, by - 42, bx - 12, by + 42), fill="#ffffff")
d.rectangle((bx - 10, by - 52, bx - 2, by + 52), fill="#ffffff")
d.rectangle((bx + 6, by - 52, bx + 14, by + 52), fill="#ffffff")
d.pieslice((bx - 20, by - 42, bx + 32, by + 4), 270, 90, fill="#ffffff")
d.pieslice((bx - 20, by - 4, bx + 36, by + 42), 270, 90, fill="#ffffff")
d.pieslice((bx - 10, by - 30, bx + 18, by - 6), 270, 90, fill="#f59e0b")
d.pieslice((bx - 10, by + 6, bx + 22, by + 30), 270, 90, fill="#f59e0b")

# 3. 左側角色：戴播客耳機的可愛橘貓
cx, cy = 225, 515
d.polygon([(cx - 95, cy - 55), (cx - 115, cy - 150), (cx - 30, cy - 105)], fill="#fb923c")
d.polygon([(cx + 95, cy - 55), (cx + 115, cy - 150), (cx + 30, cy - 105)], fill="#fb923c")
d.ellipse((cx - 115, cy - 105, cx + 115, cy + 105), fill="#fb923c")
d.ellipse((cx - 85, cy - 35, cx + 85, cy + 95), fill="#fed7aa")
# 橘貓五官與耳機
d.ellipse((cx - 52, cy - 25, cx - 26, cy + 5), fill="#1e293b")
d.ellipse((cx + 26, cy - 25, cx + 52, cy + 5), fill="#1e293b")
d.ellipse((cx - 44, cy - 18, cx - 34, cy - 8), fill="#ffffff")
d.ellipse((cx + 34, cy - 18, cx + 44, cy - 8), fill="#ffffff")
d.polygon([(cx - 12, cy + 15), (cx + 12, cy + 15), (cx, cy + 28)], fill="#f43f5e")
d.arc((cx - 128, cy - 118, cx + 128, cy + 80), 180, 360, fill="#38bdf8", width=12)
d.rounded_rectangle((cx - 142, cy - 45, cx - 108, cy + 35), radius=14, fill="#0284c7", outline="#bae6fd", width=3)

# 4. 右側角色：戴播客耳機的聰明柴犬
dx, dy = 575, 515
d.polygon([(dx - 95, dy - 60), (dx - 110, dy - 155), (dx - 30, dy - 105)], fill="#d97706")
d.polygon([(dx + 95, dy - 60), (dx + 110, dy - 155), (dx + 30, dy - 105)], fill="#d97706")
d.ellipse((dx - 115, dy - 105, dx + 115, dy + 105), fill="#eab308")
d.ellipse((dx - 88, dy - 20, dx + 88, dy + 98), fill="#fef9c3")
# 柴犬五官與耳機
d.ellipse((dx - 52, dy - 25, dx - 26, dy + 5), fill="#1e293b")
d.ellipse((dx + 26, dy - 25, dx + 52, dy + 5), fill="#1e293b")
d.ellipse((dx - 44, dy - 18, dx - 34, cy - 8), fill="#ffffff")
d.ellipse((dx + 34, dy - 18, dx + 44, cy - 8), fill="#ffffff")
d.ellipse((dx - 16, dy + 12, dx + 16, dy + 34), fill="#1e293b")
d.arc((dx - 128, dy - 118, dx + 128, dy + 80), 180, 360, fill="#f472b6", width=12)
d.rounded_rectangle((dx + 108, dy - 45, dx + 142, dy + 35), radius=14, fill="#db2777", outline="#fbcfe8", width=3)

# 5. 中央播客麥克風與對話氣泡象徵
d.rounded_rectangle((378, 560, 422, 645), radius=20, fill="#64748b", outline="#e2e8f0", width=4)
d.line(((400, 645), (400, 720)), fill="#94a3b8", width=10)
d.line(((355, 720), (445, 720)), fill="#94a3b8", width=10)

img.save("avatar.png", "PNG")
print("Avatar generated successfully!")
