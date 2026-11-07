# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `BBBike-region` source with the regional extracts from [BBBike](https://data.bbbike.org/osm/region/), parsed from their boundary JSON files.

## [1.2.3] - 2026-11-07

### Changed

- Added the covered area percentage to the `GeometryNotCoveredWarning` messages.

### Fixed

- `GeometryNotCoveredWarning` is emitted once for the whole geometry instead of once per part of a multipart geometry.

## [1.2.2] - 2026-11-04

### Added

- `progressbar` parameter accepts a custom progress bar object (`ProgressBar` protocol, same contract as in `pooch`), so other libraries can show downloads in their own progress displays.

### Fixed

- Download progress bar was never shown, because `pooch` ignores the `progressbar` argument when a custom downloader is passed.

## [1.2.1] - 2026-10-15

### Fixed

- Added automatic detection of non-available OSM extract sources and skipping them without throwing exceptions.

## [1.2.0] - 2026-09-23

### Added

- `to_geodataframe` function for `OsmExtractsIndex` class.
- `extracts_to_geodataframe` function to convert extracts to GeoDataFrame.
- `head` parameter to `display_available_extracts` function to limit displayed results.
- `sub_polygon_index` attribute to `GeometryCoveringStep` for better polygon tracking.
- Option to download test indexes from GitHub repository.

### Fixed

- Silenced warnings inside CLI when downloading results

## [1.1.0] - 2026-08-27

### Added

- CLI based on [`Typer`](https://typer.tiangolo.com/) library.

### Changed

- Internal indexes logic registration from manual to automatic.
- Simplified public api with new functions and signatures.
- Refactored geometry covering logic.
- Replaced Geod spheroid area calculations with vectorized spherical approximation without pyproj.

### Removed

- `Pyproj` from dependencies

## [1.0.1] - 2026-08-17

### Added

- Plotting function `plot` for `OsmfinderGeometryResult`.

## [1.0.0] - 2026-08-15

### Added

- Initial release: extract-catalog logic extracted from
  [QuackOSM](https://github.com/kraina-ai/quackosm) into a standalone library.
- Find and download OpenStreetMap `*.osm.pbf` extracts by name, id or geometry.
- Supported providers: Geofabrik, BBBike, OpenStreetMap.fr, Movisda (admin & grid), GEO2day.
- Public API: `find`, `get_extract_by_query`, `get_available_extracts`,
  `download`, `download_extract_by_query`, `download_extracts_pbf_files`,
  `find_smallest_containing_extracts`, `find_and_download_extracts_pbf_files`,
  `find_extracts_covering_point`, `display_available_extracts`, `clear_osm_index_cache`.

[Unreleased]: https://github.com/RaczeQ/osmfinder/compare/1.2.3...HEAD

[1.2.3]: https://github.com/RaczeQ/osmfinder/compare/1.2.2...1.2.3

[1.2.2]: https://github.com/RaczeQ/osmfinder/compare/1.2.1...1.2.2

[1.2.1]: https://github.com/RaczeQ/osmfinder/compare/1.2.0...1.2.1

[1.2.0]: https://github.com/RaczeQ/osmfinder/compare/1.1.0...1.2.0

[1.1.0]: https://github.com/RaczeQ/osmfinder/compare/1.0.1...1.1.0

[1.0.1]: https://github.com/RaczeQ/osmfinder/compare/1.0.0...1.0.1

[1.0.0]: https://github.com/RaczeQ/osmfinder/releases/tag/1.0.0
