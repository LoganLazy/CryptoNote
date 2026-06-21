from PIL import Image, ImageDraw
import os

# 创建图标 (256x256)
size = 256
img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# 绘制蓝色锁头图标 (代表加密)
# 锁体 - 圆角矩形
lock_x = size // 4
lock_y = size // 3
lock_w = size // 2
lock_h = size // 2
draw.rectangle([lock_x, lock_y, lock_x + lock_w, lock_y + lock_h],
               fill=(41, 128, 185), outline=(25, 75, 135), width=4)

# 锁钩 - 弧形
hook_center_x = size // 2
hook_center_y = lock_y - 20
hook_radius = 60
draw.arc([hook_center_x - hook_radius, hook_center_y - hook_radius,
          hook_center_x + hook_radius, hook_center_y + hook_radius],
         0, 180, fill=(41, 128, 185), width=8)

# 钥匙孔 - 小圆
keyhole_x = size // 2 - 8
keyhole_y = lock_y + lock_h // 2 - 8
draw.ellipse([keyhole_x - 15, keyhole_y - 15, keyhole_x + 15, keyhole_y + 15],
             fill=(200, 200, 200), outline=(100, 100, 100), width=2)

# 保存为各种尺寸
img.save('icon.ico', sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
print("[OK] Icon created: icon.ico")
