from pathlib import Path

import pytest
import yaml

from providency.context import PriceAnchor, PriceScale, PriceScaleError

FIXTURE = Path(__file__).parent / "fixtures" / "vector" / "no_match_real_001.yaml"


def test_real_sanitized_fixture_maps_price_within_one_tick() -> None:
    metadata = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    calibration = metadata["price_scale"]
    scale = PriceScale.from_anchors(
        [PriceAnchor(**anchor) for anchor in calibration["anchors"]],
        tick_size=calibration["tick_size"],
    )

    for probe in calibration["probes"]:
        price = scale.price_at(probe["y"])
        assert abs(price - probe["price"]) <= calibration["tick_size"]
        assert abs(scale.y_at(price) - probe["y"]) <= 0.02


@pytest.mark.parametrize(
    "anchors",
    [
        [PriceAnchor(y=10, price=100, source="DOM")],
        [
            PriceAnchor(y=10, price=100, source="DOM"),
            PriceAnchor(y=10, price=90, source="DOM"),
        ],
        [
            PriceAnchor(y=10, price=100, source="DOM"),
            PriceAnchor(y=20, price=101, source="DOM"),
        ],
    ],
)
def test_price_scale_rejects_missing_or_invalid_anchors(anchors: list[PriceAnchor]) -> None:
    with pytest.raises(PriceScaleError):
        PriceScale.from_anchors(anchors, tick_size=0.01)
