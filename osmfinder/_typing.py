from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, cast

import numpy as np
from shapely import STRtree, is_valid, make_valid

if TYPE_CHECKING:  # pragma: no cover
    from shapely.geometry.base import BaseGeometry


@dataclass
class OpenStreetMapExtract:
    """OSM Extract metadata object."""

    id: str
    name: str
    parent: str
    url: str
    geometry: "BaseGeometry"
    # The full file name is derived from the whole parent hierarchy of the index,
    # so a single extract can't know it in isolation. It's filled when the extract
    # is returned from an `OsmExtractsIndex`, not when the object is constructed.
    file_name: str = ""


class OsmExtractSource(str, Enum):
    """Enum of available OSM extract sources."""

    any = "any"
    geofabrik = "Geofabrik"
    osm_fr = "osmfr"
    bbbike = "BBBike"
    geo2day = "GEO2Day"
    movisda_admin = "Movisda-admin"
    movisda_grid = "Movisda-grid"

    @classmethod
    def _missing_(cls, value):  # type: ignore
        value = value.lower()
        for member in cls:
            if member.lower() == value:
                return member
        return None


# A single source, or multiple sources passed as an iterable or a comma-separated string.
OsmExtractSourceLike = OsmExtractSource | str | Iterable[OsmExtractSource | str]


class OsmExtractsIndex:
    """Vectorised index of all extracts in memory."""

    def __init__(
        self,
        ids: np.ndarray,
        geometries: np.ndarray,
        areas: np.ndarray,
        file_names: np.ndarray,
        names: np.ndarray,
        parents: np.ndarray,
        urls: np.ndarray,
    ):
        arrays = (ids, geometries, areas, file_names, names, parents, urls)
        lengths = {len(array) for array in arrays}
        if len(lengths) > 1:
            raise ValueError(
                "All index arrays must have the same length, got lengths:"
                f" {[len(array) for array in arrays]}."
            )

        self.ids = ids
        self.geometries = geometries
        self.areas = areas
        self.file_names = file_names
        self.names = names
        self.parents = parents
        self.urls = urls

        # Build the tree lazily for faster Shapely operations
        self._tree = None

    @classmethod
    def from_extracts(cls, extracts: list[OpenStreetMapExtract]) -> "OsmExtractsIndex":
        """
        Build a fully-populated index from a list of extracts.

        Areas are calculated, topologically invalid geometries are repaired, full file names
        are derived from the parent hierarchy, and the index is sorted by area then id
        (ascending) so that downstream selection picks the smallest-area extract first.

        Args:
            extracts (list[OpenStreetMapExtract]): List of extracts to index.

        Returns:
            OsmExtractsIndex: Fully-populated, sorted index.
        """
        ids = np.array([extract.id for extract in extracts], dtype=object)
        geometries = np.array([extract.geometry for extract in extracts], dtype=object)
        names = np.array([extract.name for extract in extracts], dtype=object)
        parents = np.array([extract.parent for extract in extracts], dtype=object)
        urls = np.array([extract.url for extract in extracts], dtype=object)

        # Fix topologically invalid geometries before computing metrics and persisting.
        geometries = _ensure_valid_geometries(geometries)

        # Calculate geodetic areas (km²).
        areas = np.array([_calculate_geodetic_area(geometry) for geometry in geometries])

        # Generate full file names from the parent hierarchy.
        file_names = _generate_file_names(ids, names, parents)

        # Sort by area then id (ascending) - lexsort is least-to-most significant.
        sort_indices = np.lexsort((ids, areas))

        return cls(
            ids=ids[sort_indices],
            geometries=geometries[sort_indices],
            areas=areas[sort_indices],
            file_names=file_names[sort_indices],
            names=names[sort_indices],
            parents=parents[sort_indices],
            urls=urls[sort_indices],
        )

    @classmethod
    def from_numpy_dict(cls, numpy_dict: dict[str, np.ndarray]) -> "OsmExtractsIndex":
        return cls(
            ids=numpy_dict["id"],
            geometries=numpy_dict["geometry"],
            areas=numpy_dict["area"],
            file_names=numpy_dict["file_name"],
            names=numpy_dict["name"],
            parents=numpy_dict["parent"],
            urls=numpy_dict["url"],
        )

    @classmethod
    def combine_indexes(cls, indexes: Sequence["OsmExtractsIndex"]) -> "OsmExtractsIndex":
        if not indexes:
            raise ValueError("Cannot combine an empty sequence of indexes.")

        # 1. Concatenate all indexes together
        combined_ids = np.concatenate([idx.ids for idx in indexes])
        combined_geometries = np.concatenate([idx.geometries for idx in indexes])
        combined_areas = np.concatenate([idx.areas for idx in indexes])
        combined_file_names = np.concatenate([idx.file_names for idx in indexes])
        combined_names = np.concatenate([idx.names for idx in indexes])
        combined_parents = np.concatenate([idx.parents for idx in indexes])
        combined_urls = np.concatenate([idx.urls for idx in indexes])

        # 2. Sort by area and id
        # np.lexsort requires reversed order of values (from least to most important)
        # First pass 'id' (second level), then 'area' (main level)
        sort_indices = np.lexsort((combined_ids, combined_areas))

        # 3. Apply the sorting indices to all arrays and return the new object
        return cls(
            ids=combined_ids[sort_indices],
            geometries=combined_geometries[sort_indices],
            areas=combined_areas[sort_indices],
            file_names=combined_file_names[sort_indices],
            names=combined_names[sort_indices],
            parents=combined_parents[sort_indices],
            urls=combined_urls[sort_indices],
        )

    @property
    def tree(self) -> STRtree:
        if self._tree is None:
            self._tree = STRtree(self.geometries)

        return self._tree

    def get_extract_by_index(self, idx: int) -> OpenStreetMapExtract:
        """Returns a single OpenStreetMapExtract object by index."""
        return OpenStreetMapExtract(
            id=str(self.ids[idx]),
            name=str(self.names[idx]),
            parent=str(self.parents[idx]),
            url=str(self.urls[idx]),
            geometry=self.geometries[idx],
            file_name=str(self.file_names[idx]),
        )

    def __iter__(self) -> Iterator[OpenStreetMapExtract]:
        """Iterate over all extracts as OpenStreetMapExtract objects."""
        for i in range(len(self.ids)):
            yield self.get_extract_by_index(i)

    def __len__(self) -> int:
        """Return the number of extracts in the index."""
        return len(self.ids)

    def filter_by_mask(self, mask: np.ndarray) -> "OsmExtractsIndex":
        """Returns new instance of the index, filtered by the mask."""
        return OsmExtractsIndex(
            ids=self.ids[mask],
            geometries=self.geometries[mask],
            areas=self.areas[mask],
            file_names=self.file_names[mask],
            names=self.names[mask],
            parents=self.parents[mask],
            urls=self.urls[mask],
        )


def _ensure_valid_geometries(geometries: np.ndarray) -> np.ndarray:
    """
    Fix topologically invalid geometries in an extracts index.

    Some sources contain invalid geometries (self-intersections, nested shells).
    These would raise ``GEOSException: TopologyException`` during the coverage
    search (intersection / difference / union).
    """
    invalid_geometries_mask = ~is_valid(geometries)
    if invalid_geometries_mask.any():
        fixed_geometries = geometries.copy()
        fixed_geometries[invalid_geometries_mask] = make_valid(geometries[invalid_geometries_mask])
        return fixed_geometries
    return geometries


def _calculate_geodetic_area(geometry: "BaseGeometry") -> float:
    from pyproj import Geod
    from shapely.ops import orient

    geod = Geod(ellps="WGS84")
    poly_area_m2, _ = geod.geometry_area_perimeter(orient(geometry, sign=1))
    poly_area_km2 = round(poly_area_m2) / 1_000_000
    return cast("float", poly_area_km2)


def _generate_file_names(ids: np.ndarray, names: np.ndarray, parents: np.ndarray) -> np.ndarray:
    """Generate full file names from the parent hierarchy of the index."""
    ids_index = {extract_id: i for i, extract_id in enumerate(ids)}

    def full_file_name(extract_id: str) -> str:
        current_id = extract_id
        parts = []
        while True:
            if current_id not in ids_index:
                parts.append(_slugify_file_name_part(current_id))
                break
            else:
                matching_row_idx = ids_index[current_id]
                parts.append(_slugify_file_name_part(names[matching_row_idx]))
                current_id = parents[matching_row_idx]

        return "_".join(parts[::-1])

    return np.array([full_file_name(extract_id) for extract_id in ids])


def _slugify_file_name_part(value: str) -> str:
    """
    Creates a slug part from file name.

    Makes it lowercase, replaces whitespace with underscores and all diactric characters into ascii.
    """
    import re

    from anyascii import anyascii

    ascii_value = re.sub(r"\s+", "_", anyascii(value).strip().lower())
    return re.sub(r"[^a-z0-9_-]+", "", ascii_value)
