"""Result classes for osmfinder find and download operations."""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from osmfinder._typing import OpenStreetMapExtract, OsmExtractSource

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry


def _format_extract_list(extracts: list[OpenStreetMapExtract], max_items: int = 5) -> str:
    if not extracts:
        return "none"
    parts = [f"{e.id} — {e.name}" for e in extracts[:max_items]]
    remaining = len(extracts) - max_items
    if remaining > 0:
        parts.append(f"+{remaining} more")
    return ", ".join(parts)


def _format_sources(sources: list[OsmExtractSource]) -> str:
    if not sources:
        return "none"
    return ", ".join(s.value for s in sources)


def _format_id_list(extracts: list[OpenStreetMapExtract], max_items: int = 5) -> str:
    if not extracts:
        return "none"
    parts = [e.id for e in extracts[:max_items]]
    remaining = len(extracts) - max_items
    if remaining > 0:
        parts.append(f"+{remaining} more")
    return ", ".join(parts)


@dataclass
class OsmfinderResult:
    """Base class for all osmfinder results."""

    extracts: list[OpenStreetMapExtract]
    sources_used: list[OsmExtractSource]

    def __repr__(self) -> str:
        sources = _format_sources(self.sources_used)
        return (
            f"{self.__class__.__name__}\n"
            f"  extracts: {_format_extract_list(self.extracts)}\n"
            f"  sources used: {sources}"
        )


@dataclass
class OsmfinderQueryResult(OsmfinderResult):
    """Result of a name/id query."""

    query: str
    matched_extracts: list[OpenStreetMapExtract]

    @property
    def extract(self) -> OpenStreetMapExtract:
        """Convenience accessor for the single matched extract."""
        if len(self.extracts) != 1:
            raise AttributeError(
                f"Expected exactly one extract, got {len(self.extracts)}."
                " Use `.extracts` to access the full list."
            )
        return self.extracts[0]

    def __repr__(self) -> str:
        multiple = len(self.matched_extracts) > 1
        extract_line = (
            f"extract: {self.extracts[0].id} — {self.extracts[0].name}"
            if not multiple
            else (
                f"extract: {self.extracts[0].id} — {self.extracts[0].name}"
                f" (selected from multiple matches)"
            )
        )
        return (
            f"{self.__class__.__name__}\n"
            f"  query: {self.query}\n"
            f"  {extract_line}\n"
            f"  matched extracts: {_format_id_list(self.matched_extracts)}\n"
            f"  sources used: {_format_sources(self.sources_used)}"
        )


@dataclass
class OsmfinderGeometryResult(OsmfinderResult):
    """Result of a geometry query."""

    input_geometry: "BaseGeometry"
    covered_geometry: "BaseGeometry"
    uncovered_geometry: "BaseGeometry"
    steps: list["GeometryCoveringStep"]
    iou_threshold: float

    def __repr__(self) -> str:
        input_area = self.input_geometry.area
        covered_area = self.covered_geometry.intersection(self.input_geometry).area
        if input_area > 0:
            coverage_pct = min(100.0, (covered_area / input_area) * 100)
        else:
            coverage_pct = 100.0 if covered_area == 0 else 0.0

        extracts_lines = (
            "\n".join(f"    {e.id} — {e.name}" for e in self.extracts)
            if self.extracts
            else "    none"
        )

        steps_lines = (
            "\n".join(
                "\n".join(f"    {line}" for line in repr(step).split("\n")) for step in self.steps
            )
            if self.steps
            else "    none"
        )

        return (
            f"{self.__class__.__name__}\n"
            f"  extracts:\n{extracts_lines}\n"
            f"  coverage: {coverage_pct:.1f}%\n"
            f"  iou threshold: {self.iou_threshold}\n"
            f"  steps:\n{steps_lines}\n"
            f"  sources used: {_format_sources(self.sources_used)}"
        )


@dataclass
class GeometryCoveringStep:
    """Record of a single extract considered during geometry covering."""

    extract: OpenStreetMapExtract
    iou: float
    selected: bool
    reason: str
    geometry_to_cover: "BaseGeometry"
    intersection_geometry: "BaseGeometry"

    def __repr__(self) -> str:
        status = "selected" if self.selected else "skipped"
        return (
            f"{self.extract.id} — {self.extract.name}\n"
            f"  iou: {self.iou:.4f}, {status}, {self.reason}"
        )


@dataclass
class OsmfinderDownloadResult:
    """Result of downloading extracts."""

    find_result: OsmfinderResult
    download_paths: list[Path]
    unavailable_extracts: list[OpenStreetMapExtract]

    @property
    def extracts(self) -> list[OpenStreetMapExtract]:
        """Convenience accessor for the nested extracts list."""
        return self.find_result.extracts

    def __repr__(self) -> str:
        if self.download_paths:
            downloaded_lines = "\n".join(f"    {path}" for path in self.download_paths)
        else:
            downloaded_lines = "    none"

        unavailable = _format_id_list(self.unavailable_extracts)

        find_result_str = str(self.find_result)
        find_result_indented = "\n".join(f"  {line}" for line in find_result_str.split("\n"))

        return (
            f"{self.__class__.__name__}\n"
            f"  downloaded:\n{downloaded_lines}\n"
            f"  unavailable:\n    {unavailable}\n"
            f"  find result:\n{find_result_indented}"
        )
