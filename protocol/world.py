"""世界尺寸：快照坐标的隐含前提。"""

WORLD_WIDTH = 720
WORLD_HEIGHT = 960

# 玩家方块是「中心点」绘制的（见 client/main.py 的 rect.center），
# 钳制时留出余量，否则走到边上会有半个身子飘出屏幕。
PLAYER_MARGIN = 20