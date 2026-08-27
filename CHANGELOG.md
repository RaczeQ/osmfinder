# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/RaczeQ/osmfinder/compare/1.1.0...HEAD

[1.1.0]: https://github.com/RaczeQ/osmfinder/compare/1.0.1...1.1.0

[1.0.1]: https://github.com/RaczeQ/osmfinder/compare/1.0.0...1.0.1

[1.0.0]: https://github.com/RaczeQ/osmfinder/releases/tag/1.0.0
