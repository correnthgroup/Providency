from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

BULLISH = (2, 209, 129)
BEARISH = (219, 57, 75)


def write_chart(
    path: Path,
    candles: list[tuple[int, int, int, int, int, tuple[int, int, int]]],
    *,
    size: tuple[int, int] = (320, 200),
) -> Path:
    image = Image.new("RGB", size, (18, 22, 28))
    draw = ImageDraw.Draw(image)
    for x, high_y, body_top, body_bottom, low_y, color in candles:
        draw.line((x, high_y, x, low_y), fill=color, width=1)
        draw.rectangle((x - 4, body_top, x + 4, body_bottom), fill=color)
    image.save(path)
    return path


def bearish_engulfing_chart(path: Path) -> Path:
    return write_chart(
        path,
        [
            (40, 135, 140, 150, 156, BULLISH),
            (90, 120, 126, 136, 143, BULLISH),
            (140, 105, 111, 121, 128, BULLISH),
            (190, 85, 92, 103, 110, BULLISH),
            (240, 78, 84, 108, 115, BEARISH),
        ],
    )
