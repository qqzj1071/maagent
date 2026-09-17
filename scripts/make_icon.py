import sys
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

sys.stdout.reconfigure(errors="replace")

SRC = Path(r"D:\install\maagent图标.png")
OUT_DIR = Path(__file__).resolve().parent.parent / "gameops" / "gui" / "assets"
WHITE = 235


def remove_background(img: Image.Image) -> Image.Image:
    img = img.convert("RGBA")
    arr = np.array(img)
    h, w = arr.shape[:2]
    rgb = arr[:, :, :3].astype(np.int16)

    def is_bg(y: int, x: int) -> bool:
        r, g, b = rgb[y, x]
        return r >= WHITE and g >= WHITE and b >= WHITE

    visited = np.zeros((h, w), dtype=bool)
    q: deque[tuple[int, int]] = deque()
    for x in range(w):
        for y in (0, h - 1):
            if is_bg(y, x) and not visited[y, x]:
                visited[y, x] = True
                q.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if is_bg(y, x) and not visited[y, x]:
                visited[y, x] = True
                q.append((y, x))

    while q:
        y, x = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] and is_bg(ny, nx):
                visited[ny, nx] = True
                q.append((ny, nx))

    arr[visited, 3] = 0
    return Image.fromarray(arr)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    img = Image.open(SRC)
    print("source:", img.size, img.mode)
    cut = remove_background(img)
    png_path = OUT_DIR / "icon.png"
    cut.save(png_path)
    cut.save(OUT_DIR / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("saved:", png_path, "and icon.ico")
    return 0


if __name__ == "__main__":
    sys.exit(main())
