"""Result classes for osmfinder find and download operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from matplotlib.axes import Axes
    from shapely.geometry.base import BaseGeometry

    from osmfinder._typing import OpenStreetMapExtract, OsmExtractSource


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


@dataclass(kw_only=True)
class OsmfinderResult:
    """Base class for all osmfinder results."""

    extracts: list[OpenStreetMapExtract]
    sources_used: list[OsmExtractSource]
    config: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        sources = _format_sources(self.sources_used)
        return (
            f"{self.__class__.__name__}\n"
            f"  extracts: {_format_extract_list(self.extracts)}\n"
            f"  sources used: {sources}"
        )

    def download(
        self,
        download_directory: str | Path = "files",
        progressbar: bool = True,
        force_refresh: bool = False,
        retry_on_unavailable: bool = True,
    ) -> OsmfinderDownloadResult:
        """
        Download all extracts in this result as PBF files.

        When ``retry_on_unavailable`` is ``True`` (the default), unavailable extracts
        are excluded and the search is retried. On success, ``download_paths`` contains
        only the extracts downloaded in the final successful attempt; ``unavailable_extracts``
        accumulates all extracts that failed across every attempt.

        Args:
            download_directory (str | Path): Directory where PBF files should be saved.
                Defaults to "files".
            progressbar (bool): Show progress bar. Defaults to True.
            force_refresh (bool): When ``True``, re-download even if the file already
                exists. Defaults to False.
            retry_on_unavailable (bool): When ``True`` and this is a query or geometry
                result, unavailable extracts are excluded and the search is retried. When
                ``False``, the result's extracts are downloaded as-is and unavailable extracts
                raise an exception. Defaults to ``True``.

        Returns:
            OsmfinderDownloadResult: Result containing downloaded paths and find result.
        """
        from osmfinder.finder import download

        return download(
            self,
            download_directory=download_directory,
            progressbar=progressbar,
            force_refresh=force_refresh,
            retry_on_unavailable=retry_on_unavailable,
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

    input_geometry: BaseGeometry
    covered_geometry: BaseGeometry
    uncovered_geometry: BaseGeometry
    steps: list[GeometryCoveringStep]

    @property
    def iou_threshold(self) -> float:
        return float(self.config["geometry_coverage_iou_threshold"])

    @property
    def coverage(self) -> float:
        input_area = self.input_geometry.area
        covered_area = self.covered_geometry.intersection(self.input_geometry).area
        if input_area > 0:
            coverage_pct = min(1.0, covered_area / input_area)
        else:
            coverage_pct = 1.0 if covered_area == 0 else 0.0

        return float(coverage_pct)

    def __repr__(self) -> str:
        coverage_pct = self.coverage * 100

        extracts_lines = (
            "\n".join(f"    {e.id} — {e.name}" for e in self.extracts)
            if self.extracts
            else "    none"
        )

        steps_lines = (
            "\n".join(
                f"    {step.extract.id} — {step.extract.name}\n"
                f"      iou: {step.iou:.4f}, "
                f"{'selected' if step.selected else 'skipped'}, {step.reason}"
                for step in self.steps
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

    def plot(self, ax: Axes | None = None, legend: bool = True) -> Axes:
        """
        Plot extracts with input geometry.

        Uses Matplotlib and Geopandas to plot the geometries.

        Args:
            ax (Axes, optional): Matplotlib axes to use for plotting. Defaults to None.
            legend (bool, optional): Show legend. Defaults to True.

        Returns:
            Axes: Matplotlib axes with plotted geometries.
        """
        try:
            import geopandas as gpd
            import matplotlib.patches as mpatches
            import matplotlib.pyplot as plt
        except ImportError as ex:
            raise ImportError(
                "The geopandas and matplotlib packages are required for plotting the results. "
                "You can install it using 'conda install -c conda-forge matplotlib geopandas' or "
                "'pip install matplotlib geopandas'."
            ) from ex

        if ax is None:
            _, ax = plt.subplots()

        ex_gs = gpd.GeoSeries([extract.geometry for extract in self.extracts], crs=4326)
        # outlines only + transparency, so overlapping extracts show up as several borders
        ex_gs.plot(ax=ax, color="tab:blue", alpha=0.1)
        ex_gs.boundary.plot(ax=ax, color="tab:blue", linewidth=1.2, alpha=0.6)

        q_gs = gpd.GeoSeries([self.input_geometry], crs=4326)

        q_gs.plot(
            ax=ax,
            color=(0, 0, 0, 0),
            zorder=2,
            hatch="///",
            edgecolor="orange",
            linewidth=1.5,
        )
        total_extracts = len(self.extracts)
        extract_label = "extract" if total_extracts == 1 else "extracts"
        coverage_pct = self.coverage * 100
        ax.set_title(f"{total_extracts} {extract_label} ({coverage_pct:.1f}% coverage)")

        if legend:
            blue_patch = mpatches.Patch(
                edgecolor=("tab:blue", 0.6), facecolor=("tab:blue", 0.1), label="OSM extract"
            )
            orange_patch = mpatches.Patch(
                facecolor=(0, 0, 0, 0),
                edgecolor="orange",
                hatch="///",
                linewidth=1.5,
                label="Geometry query",
            )
            ax.legend(handles=[orange_patch, blue_patch], loc="best")

        return ax


@dataclass
class GeometryCoveringStep:
    """Record of a single extract considered during geometry covering."""

    extract: OpenStreetMapExtract
    iou: float
    selected: bool
    reason: str
    geometry_to_cover: BaseGeometry
    intersection_geometry: BaseGeometry
    cumulative_coverage: float = 0.0

    def __repr__(self) -> str:
        return f"GeometryCoveringStep({self.extract.id}, {self.extract.name})"


@dataclass
class OsmfinderDownloadResult:
    """
    Result of downloading extracts.

    ``download_paths`` contains only the extracts from the final successful attempt.
    ``unavailable_extracts`` accumulates all extracts that failed across every retry attempt.
    """

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
