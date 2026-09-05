from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from providency.patterns import Candle, CandleBox, VisualCandle

DETECTOR_VERSION = "0.8.0"


class VisionDetectionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VisionConfig:
    bullish_rgb: tuple[int, int, int] = (1, 201, 126)
    bearish_rgb: tuple[int, int, int] = (208, 54, 70)
    color_tolerance: int = 15
    min_image_width: int = 200
    min_image_height: int = 120
    max_image_width: int = 8192
    max_image_height: int = 8192
    max_image_pixels: int = 20_000_000
    min_body_width: int = 4
    min_body_height: int = 1
    min_range_height: int = 8
    max_component_width: int = 40


@dataclass(frozen=True, slots=True)
class VisionWindow:
    image_width: int
    image_height: int
    candles: tuple[VisualCandle, ...]


class CandleDetector:
    def __init__(self, config: VisionConfig | None = None) -> None:
        self.config = config or VisionConfig()

    def detect(self, path: Path) -> VisionWindow:
        if not path.is_file():
            raise VisionDetectionError("Screenshot is missing.")
        try:
            with Image.open(path) as source:
                width, height = source.size
                if (
                    width > self.config.max_image_width
                    or height > self.config.max_image_height
                    or width * height > self.config.max_image_pixels
                ):
                    raise VisionDetectionError("Screenshot dimensions exceed the safe limit.")
                rgb = np.asarray(source.convert("RGB"))
        except VisionDetectionError:
            raise
        except (OSError, ValueError) as exc:
            raise VisionDetectionError("Screenshot cannot be decoded.") from exc
        if width < self.config.min_image_width or height < self.config.min_image_height:
            raise VisionDetectionError("Screenshot is too small for candle geometry.")

        detected: list[VisualCandle] = []
        for color, label in (
            (self.config.bullish_rgb, "BULLISH"),
            (self.config.bearish_rgb, "BEARISH"),
        ):
            detected.extend(self._components(rgb, color, label, height))
        detected.sort(key=lambda item: item.box.x)
        detected = self._merge_same_color_fragments(detected, height)
        if not detected:
            raise VisionDetectionError("No traditional candles were found in the screenshot.")
        for left, right in zip(detected, detected[1:], strict=False):
            if left.box.x + left.box.width > right.box.x:
                raise VisionDetectionError("Candle geometry overlaps and is ambiguous.")
        return VisionWindow(width, height, tuple(detected))

    @staticmethod
    def _merge_same_color_fragments(
        detected: list[VisualCandle], image_height: int
    ) -> list[VisualCandle]:
        merged: list[VisualCandle] = []
        for item in detected:
            if not merged or merged[-1].box.x + merged[-1].box.width <= item.box.x:
                merged.append(item)
                continue
            previous = merged[-1]
            if previous.color != item.color:
                merged.append(item)
                continue
            left = min(previous.box.x, item.box.x)
            right = max(
                previous.box.x + previous.box.width,
                item.box.x + item.box.width,
            )
            top = min(previous.box.y, item.box.y)
            bottom = max(
                previous.box.y + previous.box.height,
                item.box.y + item.box.height,
            )
            body_left = min(previous.box.body_x, item.box.body_x)
            body_right = max(
                previous.box.body_x + previous.box.body_width,
                item.box.body_x + item.box.body_width,
            )
            body_top = min(previous.box.body_y, item.box.body_y)
            body_bottom = max(
                previous.box.body_y + previous.box.body_height,
                item.box.body_y + item.box.body_height,
            )
            high = float(image_height - top)
            low = float(image_height - bottom)
            body_high = float(image_height - body_top)
            body_low = float(image_height - body_bottom)
            candle = (
                Candle(open=body_low, high=high, low=low, close=body_high)
                if item.color == "BULLISH"
                else Candle(open=body_high, high=high, low=low, close=body_low)
            )
            merged[-1] = VisualCandle(
                candle=candle,
                box=CandleBox(
                    x=left,
                    y=top,
                    width=right - left,
                    height=bottom - top,
                    body_x=body_left,
                    body_y=body_top,
                    body_width=body_right - body_left,
                    body_height=body_bottom - body_top,
                ),
                color=item.color,
            )
        return merged

    def _components(
        self,
        rgb: Any,
        color: tuple[int, int, int],
        label: str,
        image_height: int,
    ) -> list[VisualCandle]:
        target = np.array(color, dtype=np.int16)
        tolerance = self.config.color_tolerance
        lower = np.clip(target - tolerance, 0, 255).astype(np.uint8)
        upper = np.clip(target + tolerance, 0, 255).astype(np.uint8)
        mask = cv2.inRange(rgb, lower, upper)
        closed_mask = cv2.morphologyEx(
            mask,
            cv2.MORPH_CLOSE,
            np.ones((3, 1), dtype=np.uint8),
        )
        body_mask = cv2.morphologyEx(
            closed_mask,
            cv2.MORPH_OPEN,
            np.ones((1, 3), dtype=np.uint8),
        )
        count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
            body_mask, connectivity=8
        )
        candles: list[VisualCandle] = []
        for component in range(1, count):
            x, y, width, height, _area = (int(value) for value in stats[component])
            if (
                width < self.config.min_body_width
                or width > self.config.max_component_width
                or height < self.config.min_body_height
            ):
                continue
            center_left = x + max(0, width // 2 - 1)
            center_right = min(mask.shape[1], center_left + 3)
            vertical_rows = np.flatnonzero(
                closed_mask[:, center_left:center_right].any(axis=1)
            )
            if vertical_rows.size == 0:
                continue
            runs = np.split(vertical_rows, np.where(np.diff(vertical_rows) > 1)[0] + 1)
            body_midpoint = y + height // 2
            containing = [
                run
                for run in runs
                if int(run[0]) <= body_midpoint <= int(run[-1])
            ]
            if not containing:
                continue
            candle_rows = containing[0]
            candle_top = int(candle_rows[0])
            candle_bottom = int(candle_rows[-1])
            range_height = candle_bottom - candle_top + 1
            if range_height < self.config.min_range_height:
                continue
            high = float(image_height - candle_top)
            low = float(image_height - (candle_bottom + 1))
            body_high = float(image_height - y)
            body_low = float(image_height - (y + height))
            candle = (
                Candle(open=body_low, high=high, low=low, close=body_high)
                if label == "BULLISH"
                else Candle(open=body_high, high=high, low=low, close=body_low)
            )
            candles.append(
                VisualCandle(
                    candle=candle,
                    box=CandleBox(
                        x=x,
                        y=candle_top,
                        width=width,
                        height=range_height,
                        body_x=x,
                        body_y=y,
                        body_width=width,
                        body_height=height,
                    ),
                    color=label,
                )
            )
        return candles
