"""assets/icon.ico 생성 (스로틀 게이지 모양). 다시 만들 때: python tools/make_icon.py"""

from pathlib import Path

from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((8, 8, S - 8, S - 8), radius=52, fill="#1d2433")
# 게이지 바탕 호와 호버 지점까지 채운 호
box = (44, 52, S - 44, S - 36)
d.arc(box, start=150, end=390, fill="#3b4558", width=26)
d.arc(box, start=150, end=250, fill="#2a78d6", width=26)
# 호버 지점 표시
d.arc(box, start=246, end=254, fill="#eb6834", width=40)
# 바늘 중심
c = (S // 2, (box[1] + box[3]) // 2)
d.ellipse((c[0] - 16, c[1] - 16, c[0] + 16, c[1] + 16), fill="#f0efec")
d.line((c, (c[0] - 34, c[1] - 62)), fill="#f0efec", width=12)
# 아래 막대 (호버 구간)
d.rounded_rectangle((70, 196, S - 70, 214), radius=9, fill="#1baf7a")

out = Path(__file__).resolve().parents[1] / "assets" / "icon.ico"
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.save(out.with_suffix(".png"))
print(out)
