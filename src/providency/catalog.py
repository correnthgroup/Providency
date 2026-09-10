# ruff: noqa: E501
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from providency.patterns import PatternPackage, PatternPackageError


@dataclass(frozen=True, slots=True)
class PatternCatalogItem:
    package: PatternPackage
    path: Path
    positive_samples: int
    negative_samples: int
    forming_samples: int
    illustration_label: str = "Ilustração didática"

    @property
    def quality(self) -> dict[str, Any]:
        reviewed = self.package.observed_precision
        return {
            "editorial_confidence": self.package.research_confidence,
            "geometric_result": "Amostra insuficiente",
            "observed_precision": reviewed if reviewed is not None else "Amostra insuficiente",
            "sample_count": self.positive_samples + self.negative_samples,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.package.to_catalog_dict(),
            "path": str(self.path),
            "enabled": self.package.enabled,
            "illustration": {"label": self.illustration_label, "path": None},
            "quality": self.quality,
        }


class PatternCatalogError(ValueError):
    pass


class PatternCatalog:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.items: tuple[PatternCatalogItem, ...] = self._load()

    def _load(self) -> tuple[PatternCatalogItem, ...]:
        if not self.root.is_dir():
            raise PatternCatalogError(f"Catálogo de padrões não encontrado: {self.root}.")
        items: list[PatternCatalogItem] = []
        seen: set[tuple[str, str]] = set()
        for directory in sorted(self.root.iterdir()):
            if not directory.is_dir() or directory.name.startswith("_"):
                continue
            pattern_file = directory / "pattern.yaml"
            if not pattern_file.is_file():
                raise PatternCatalogError(f"Pacote sem pattern.yaml: {directory.name}.")
            try:
                package = PatternPackage.load(pattern_file)
            except PatternPackageError as exc:
                raise PatternCatalogError(f"{directory.name}: {exc}") from exc
            key = (package.id, package.version)
            if key in seen:
                raise PatternCatalogError(f"Pacote duplicado: {package.id} {package.version}.")
            seen.add(key)
            dataset = directory / "dataset.yaml"
            positive, negative, forming = self._sample_counts(dataset)
            items.append(PatternCatalogItem(package, pattern_file, positive, negative, forming))
        if not items:
            raise PatternCatalogError("O catálogo de padrões está vazio.")
        return tuple(items)

    @staticmethod
    def _sample_counts(dataset: Path) -> tuple[int, int, int]:
        if not dataset.is_file():
            return 0, 0, 0
        try:
            raw = yaml.safe_load(dataset.read_text(encoding="utf-8")) or {}
            cases = (
                raw.get("assets", raw.get("cases", raw.get("datasets", [])))
                if isinstance(raw, dict)
                else []
            )
            if not isinstance(cases, list):
                cases = []
            counts = {"positive": 0, "negative": 0, "forming": 0}
            for item in cases:
                if isinstance(item, dict):
                    label = str(item.get("label", "")).lower()
                    name = str(item.get("path", item.get("file", ""))).lower()
                    for key in counts:
                        if key == label or key in name:
                            counts[key] += 1
            return counts["positive"], counts["negative"], counts["forming"]
        except (OSError, yaml.YAMLError):
            return 0, 0, 0

    @property
    def enabled(self) -> tuple[PatternCatalogItem, ...]:
        return tuple(item for item in self.items if item.package.enabled)

    def validate_enabled(self) -> None:
        if not self.enabled:
            raise PatternCatalogError("Habilite pelo menos um pacote de padrão.")

    def by_id(self, pattern_id: str) -> PatternCatalogItem | None:
        return next((item for item in self.items if item.package.id == pattern_id), None)

    def fingerprint(self) -> str:
        payload = "\n".join(
            f"{item.package.id}:{item.package.version}:{item.package.enabled}"
            for item in self.items
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_list(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.items]
