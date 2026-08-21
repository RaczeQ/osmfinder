"""CLI for searching and downloading OpenStreetMap extracts."""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from osmfinder._typing import OsmExtractSource
from osmfinder.exceptions import (
    OsmExtractMultipleMatchesError,
    OsmExtractZeroMatchesError,
)
from osmfinder.extract import clear_osm_index_cache
from osmfinder.finder import (
    _get_index_for_sources,
    display_available_extracts,
    find_extract_by_query,
    find_extracts_by_geometry,
)

if TYPE_CHECKING:
    from osmfinder._results import OsmfinderGeometryResult, OsmfinderQueryResult

app = typer.Typer(
    context_settings={"help_option_names": ["-h", "--help"]},
    rich_markup_mode="rich",
)

err_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        from osmfinder import __version__

        typer.echo(f"osmfinder {__version__}")
        raise typer.Exit()


def _parse_single_source(source: str) -> str:
    if "," in source:
        return "any"
    try:
        OsmExtractSource(source)
    except ValueError as ex:
        raise typer.BadParameter(f"Unknown OSM extracts source: {source}.") from ex
    return source


def _extracts_from_full_names(full_names: list[str]) -> list[Any]:
    """Look up extracts in the index by their full file names."""
    try:
        index = _get_index_for_sources("any")
    except Exception:
        return []

    extracts = []
    file_names_lower = [str(fn).lower() for fn in index.file_names]
    for full_name in full_names:
        try:
            idx = file_names_lower.index(str(full_name).lower())
            extracts.append(index.get_extract_by_index(idx))
        except ValueError:
            continue
    return extracts


@app.command("list")  # type: ignore[misc]
def list_cmd(
    source: Annotated[
        str,
        typer.Option(
            "--source",
            "-s",
            help=(
                "Source of extracts to display. Can be one of: 'any', 'Geofabrik', 'BBBike',"
                " 'osmfr', 'GEO2Day', 'Movisda-admin', 'Movisda-grid'."
            ),
        ),
    ] = "any",
    full_names: Annotated[
        bool,
        typer.Option(
            "--full-names/--short-names",
            help="Display full extract names (with parent hierarchy) or short names.",
        ),
    ] = True,
    pager: Annotated[
        bool,
        typer.Option(
            "--pager/--no-pager",
            help="Use a pager for long output.",
        ),
    ] = True,
) -> None:
    """Display available OSM extracts as a tree."""
    display_source = _parse_single_source(source)
    if "," in source:
        err_console.print("[yellow]Multiple sources selected; displaying all extracts.[/yellow]")
    display_available_extracts(
        source=display_source,
        use_full_names=full_names,
        use_pager=pager,
    )


def _print_query_table(matched_extracts: list[Any], extracts: list[Any], query: str) -> None:
    """Print a Rich table of matched extracts, highlighting selected ones."""
    from osmfinder._typing import _calculate_geodetic_area

    extract_areas = {e.id: _calculate_geodetic_area(e.geometry) for e in matched_extracts}
    matched_extracts = sorted(
        matched_extracts,
        key=lambda e: (extract_areas[e.id], e.id),
    )
    table = Table(
        title=f"Query: [bold cyan]{query}[/bold cyan]",
        show_header=True,
        header_style="bold magenta",
        title_style="bold blue",
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("Selected", width=10)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="green")
    table.add_column("File name", style="blue")
    table.add_column("Area (km\u00b2)", justify="right", style="yellow")

    selected_ids = {e.id for e in extracts}

    for idx, extract in enumerate(matched_extracts, 1):
        is_selected = extract.id in selected_ids
        selected_str = "[bold green]:heavy_check_mark:[/bold green]" if is_selected else ""
        table.add_row(
            str(idx),
            selected_str,
            extract.id,
            extract.name,
            extract.file_name,
            f"{extract_areas[extract.id]:.2f}",
        )

    from rich.console import Console

    console = Console()
    console.print(table)


def _print_query_result(result: OsmfinderQueryResult) -> None:
    """Print a query result as a Rich table, highlighting the selected extract."""
    _print_query_table(result.matched_extracts, result.extracts, result.query)


def _print_zero_matches(query: str, full_names: list[str]) -> None:
    """Print a table of suggested extracts when no match is found."""
    suggested = _extracts_from_full_names(full_names)
    if suggested:
        _print_query_table(suggested, [], query)
        err_console.print(
            f"[red]Error:[/red] Zero extracts matched query [bold]'{query}'[/bold]."
            f" Found {len(suggested)} close match(es)."
        )
    else:
        err_console.print(
            f"[red]Error:[/red] Zero extracts matched query [bold]'{query}'[/bold]."
            " Zero close matches have been found."
        )


def _print_multiple_matches(matched_extracts: list[Any], query: str) -> None:
    """Print a table of all matching extracts when multiple matches are found."""
    _print_query_table(matched_extracts, [], query)
    err_console.print(
        f"[red]Error:[/red] Multiple extracts matched query [bold]'{query}'[/bold]."
        f" Found {len(matched_extracts)} matches."
        " Use the full name as a query or set [bold]--select-first-match[/bold]"
        " to control this behaviour."
    )


def _print_geometry_result(result: OsmfinderGeometryResult) -> None:
    """Print a geometry result as a Rich table."""
    table = Table(
        title="Geometry covering result",
        show_header=True,
        header_style="bold magenta",
        title_style="bold blue",
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="green")
    table.add_column("Area (km\u00b2)", justify="right", style="yellow")
    table.add_column("IoU", justify="right")
    table.add_column("Status", style="bold")
    table.add_column("Reason", style="dim")

    from osmfinder._typing import _calculate_geodetic_area

    row_idx = 1
    for step in result.steps:
        if not step.selected:
            continue
        area = _calculate_geodetic_area(step.extract.geometry)
        table.add_row(
            str(row_idx),
            step.extract.id,
            step.extract.name,
            f"{area:.2f}",
            f"{step.iou:.4f}",
            "[bold green]selected[/bold green]",
            step.reason,
        )
        row_idx += 1

    from rich.console import Console

    console = Console()
    console.print(table)

    input_area = _calculate_geodetic_area(result.input_geometry)
    covered_area = _calculate_geodetic_area(
        result.covered_geometry.intersection(result.input_geometry)
    )
    coverage_pct = min(100.0, (covered_area / input_area) * 100) if input_area > 0 else 100.0

    summary = (
        f"[bold]Coverage:[/bold] {coverage_pct:.1f}%\n"
        f"[bold]IoU threshold:[/bold] {result.iou_threshold}\n"
        f"[bold]Sources used:[/bold] {', '.join(s.value for s in result.sources_used)}"
    )
    console.print(Panel(summary, title="Summary", border_style="blue"))


@app.command("search")  # type: ignore[misc]
def search_cmd(
    query: Annotated[
        str,
        typer.Argument(help="Query to search for an extract by name or id."),
    ],
    source: Annotated[
        str,
        typer.Option(
            "--source",
            "-s",
            help=(
                "Source(s) to search. Can be one of: 'any', 'Geofabrik', 'BBBike', 'osmfr',"
                " 'GEO2Day', 'Movisda-admin', 'Movisda-grid', or a comma-separated combination"
                " (e.g. 'bbbike,osmfr')."
            ),
            show_default=True,
        ),
    ] = "any",
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Directory where the PBF file should be downloaded. Defaults to 'files'.",
            exists=False,
            file_okay=False,
            dir_okay=True,
            writable=True,
            resolve_path=True,
            show_default=False,
        ),
    ] = None,
    select_first_match: Annotated[
        bool,
        typer.Option(
            "--select-first-match/--no-select-first-match",
            help=(
                "When multiple extracts match the query, select the first one (smallest area)"
                " with a warning instead of raising an error."
            ),
        ),
    ] = True,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/",
            help="Display results without downloading.",
        ),
    ] = False,
    progressbar: Annotated[
        bool,
        typer.Option(
            "--progressbar/--no-progressbar",
            help="Show download progress bar.",
        ),
    ] = True,
) -> None:
    """Search for an OpenStreetMap extract by name or id."""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore")
        try:
            result = find_extract_by_query(
                query,
                source=source,
                select_first_match=select_first_match,
            )
        except OsmExtractZeroMatchesError as ex:
            _print_zero_matches(query, ex.matching_full_names)
            raise typer.Exit(code=1) from None
        except OsmExtractMultipleMatchesError as ex:
            matched = _extracts_from_full_names(ex.matching_full_names)
            _print_multiple_matches(matched, query)
            raise typer.Exit(code=1) from None
        except Exception as ex:
            err_console.print(f"[red]Error:[/red] {ex}")
            raise typer.Exit(code=1) from None

    _print_query_result(result)

    if not dry_run:
        err_console.print(
            f"[bold blue]Downloading {len(result.extracts)} extract(s)...[/bold blue]"
        )
        try:
            dl_result = result.download(
                download_directory=output or Path("files"),
                progressbar=progressbar,
            )
            for path in dl_result.download_paths:
                err_console.print(f"[green]Downloaded:[/green] {path}")
        except Exception as ex:
            err_console.print(f"[red]Download error:[/red] {ex}")
            raise typer.Exit(code=1) from None


@app.command("covers")  # type: ignore[misc]
def covers_cmd(
    bbox: Annotated[
        str | None,
        typer.Option(
            "--bbox",
            help="Bounding box as comma-separated numbers: lon1,lat1,lon2,lat2",
            show_default=False,
        ),
    ] = None,
    wkt: Annotated[
        str | None,
        typer.Option(
            "--wkt",
            help="Geometry in WKT format.",
            show_default=False,
        ),
    ] = None,
    geojson: Annotated[
        str | None,
        typer.Option(
            "--geojson",
            help="Geometry in GeoJSON format.",
            show_default=False,
        ),
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option(
            "--file",
            help="Path to a geospatial file (GeoJSON, Shapefile, etc.).",
            exists=True,
            readable=True,
            resolve_path=True,
            show_default=False,
        ),
    ] = None,
    source: Annotated[
        str,
        typer.Option(
            "--source",
            "-s",
            help=(
                "Source(s) to search. Can be one of: 'any', 'Geofabrik', 'BBBike', 'osmfr',"
                " 'GEO2Day', 'Movisda-admin', 'Movisda-grid', or a comma-separated combination"
                " (e.g. 'bbbike,osmfr')."
            ),
            show_default=True,
        ),
    ] = "any",
    iou_threshold: Annotated[
        float,
        typer.Option(
            "--iou-threshold",
            help="Minimal Intersection over Union threshold for selecting extracts.",
            min=0.0,
            max=1.0,
            show_default=True,
        ),
    ] = 0.01,
    allow_uncovered_geometry: Annotated[
        bool,
        typer.Option(
            "--allow-uncovered-geometry/",
            help="Suppress error if some geometry parts are not covered by any extract.",
        ),
    ] = False,
    single_result: Annotated[
        bool,
        typer.Option(
            "--single-result/",
            help="Return only the single best extract covering the geometry.",
        ),
    ] = False,
    single_iou_threshold: Annotated[
        float,
        typer.Option(
            "--single-iou-threshold",
            help="Minimal IoU for selecting a single result.",
            min=0.0,
            max=1.0,
            show_default=True,
        ),
    ] = 0.99,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Directory where matching PBF files should be downloaded. Defaults to 'files'.",
            exists=False,
            file_okay=False,
            dir_okay=True,
            writable=True,
            resolve_path=True,
            show_default=False,
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/",
            help="Display results without downloading.",
        ),
    ] = False,
    progressbar: Annotated[
        bool,
        typer.Option(
            "--progressbar/--no-progressbar",
            help="Show download progress bar.",
        ),
    ] = True,
) -> None:
    """Find OpenStreetMap extracts covering a geometry."""
    from shapely.geometry import box as shapely_box

    geometry = None
    geom_inputs = [bbox, wkt, geojson, file]
    if sum(item is not None for item in geom_inputs) > 1:
        err_console.print("[red]Error:[/red] Provide only one geometry input.")
        raise typer.Exit(code=1)

    if bbox is not None:
        try:
            bbox_values = [float(x.strip()) for x in bbox.split(",")]
            geometry = shapely_box(*bbox_values)
        except ValueError:
            err_console.print(
                "[red]Error:[/red] Invalid bbox format. Expected: lon1,lat1,lon2,lat2"
            )
            raise typer.Exit(code=1) from None
    elif wkt is not None:
        try:
            from shapely import from_wkt

            geometry = from_wkt(wkt)
        except Exception:
            err_console.print("[red]Error:[/red] Cannot parse WKT geometry.")
            raise typer.Exit(code=1) from None
    elif geojson is not None:
        try:
            from shapely import from_geojson

            geometry = from_geojson(geojson)
        except Exception:
            err_console.print("[red]Error:[/red] Cannot parse GeoJSON geometry.")
            raise typer.Exit(code=1) from None
    elif file is not None:
        try:
            import geopandas as gpd

            gdf = gpd.read_file(file)
            try:
                geometry = gdf.union_all()
            except AttributeError:
                geometry = gdf.unary_union
        except Exception:
            err_console.print(f"[red]Error:[/red] Cannot parse geometry from file: {file}")
            raise typer.Exit(code=1) from None
    else:
        err_console.print(
            "[red]Error:[/red] No geometry provided. Use --bbox, --wkt, --geojson, or --file."
        )
        raise typer.Exit(code=1)

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore")
        try:
            result = find_extracts_by_geometry(
                geometry,
                source=source,
                geometry_coverage_iou_threshold=iou_threshold,
                allow_uncovered_geometry=allow_uncovered_geometry,
                force_single_result=single_result,
                single_result_iou_threshold=single_iou_threshold,
            )
        except Exception as ex:
            err_console.print(f"[red]Error:[/red] {ex}")
            raise typer.Exit(code=1) from None

    _print_geometry_result(result)

    if not dry_run and result.extracts:
        err_console.print(
            f"[bold blue]Downloading {len(result.extracts)} extract(s)...[/bold blue]"
        )
        try:
            dl_result = result.download(
                download_directory=output or Path("files"),
                progressbar=progressbar,
            )
            for path in dl_result.download_paths:
                err_console.print(f"[green]Downloaded:[/green] {path}")
        except Exception as ex:
            err_console.print(f"[red]Download error:[/red] {ex}")
            raise typer.Exit(code=1) from None


@app.command("clear")  # type: ignore[misc]
def clear_cache_cmd(
    source: Annotated[
        str | None,
        typer.Option(
            "--source",
            "-s",
            help="Source to clear cache for. If not provided, clears all sources.",
        ),
    ] = None,
) -> None:
    """Clear the local index cache."""
    if source is not None:
        try:
            source_enum = OsmExtractSource(source)
        except ValueError:
            err_console.print(f"[red]Error:[/red] Unknown OSM extracts source: {source}.")
            raise typer.Exit(code=1) from None
        clear_osm_index_cache(extract_source=source_enum)
        err_console.print(f"[green]Cache cleared for {source_enum.value}.[/green]")
    else:
        clear_osm_index_cache()
        err_console.print("[green]All caches cleared.[/green]")


@app.callback()  # type: ignore[misc]
def main(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            "-v",
            help="Show the application's version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = None,
) -> None:
    """
    Osmfinder CLI.

    Find and download OpenStreetMap [bold green]*.osm.pbf[/bold green] extracts by name, id or
    geometry.
    """


if __name__ == "__main__":
    app()
