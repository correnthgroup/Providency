# ruff: noqa: E501
from pathlib import Path

from providency.catalog import PatternCatalog


def test_catalog_loads_all_current_packages_without_hardcoding_enabled_set() -> None:
    catalog = PatternCatalog(Path(__file__).parents[1] / "patterns")
    assert {item.package.id for item in catalog.items} == {
        "bearish_engulfing",
        "evening_star",
        "morning_star",
        "three_black_crows",
        "three_outside_up",
        "three_white_soldiers",
    }
    assert [item.package.id for item in catalog.enabled] == ["bearish_engulfing"]
    assert catalog.by_id("evening_star") is not None
    assert all(item.illustration_label == "Ilustração didática" for item in catalog.items)
