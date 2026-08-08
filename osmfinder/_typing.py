from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

import numpy as np
from shapely import STRtree

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
        self.ids = ids
        self.geometries = geometries
        self.areas = areas
        self.file_names = file_names
        self.names = names
        self.parents = parents
        self.urls = urls

        # Build the tree for faster Shapely operations
        self._tree = None

    @staticmethod
    def from_numpy_dict(numpy_dict: dict[str, np.ndarray]) -> "OsmExtractsIndex":
        return OsmExtractsIndex(
            ids=numpy_dict["id"],
            geometries=numpy_dict["geometry"],
            areas=numpy_dict["area"],
            file_names=numpy_dict["file_name"],
            names=numpy_dict["name"],
            parents=numpy_dict["parent"],
            urls=numpy_dict["url"],
        )

    @staticmethod
    def combine_indexes(indexes: Sequence["OsmExtractsIndex"]) -> "OsmExtractsIndex":
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
        return OsmExtractsIndex(
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