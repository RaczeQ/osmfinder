"""Shared helper functions for the osmfinder test suite.

These helpers build :class:`OsmExtractsIndex` instances from simple dict
records.  They are kept in a regular importable module (rather than
``conftest.py``) so that individual test modules can import them directly,
which is required when ``--import-mode=importlib`` is used.
"""

from typing import Any

import numpy as np
from shapely.geometry import box

from osmfinder._typing import OsmExtractsIndex


def _index_from_records(records: list[dict[str, Any]]) -> OsmExtractsIndex:
    """Build an :class:`OsmExtractsIndex` from a list of record dicts.

    Unlike ``OsmExtractsIndex.from_extracts``, the ``area`` and ``file_name``
    values provided in the records are preserved verbatim (they are not
    recalculated from the geometry / parent hierarchy).
    """
    return OsmExtractsIndex(
        ids=np.array([record["id"] for record in records], dtype=object),
        geometries=np.array([record["geometry"] for record in records], dtype=object),
        areas=np.array([record.get("area", 0.0) for record in records]),
        file_names=np.array(
            [record.get("file_name", record["id"]) for record in records], dtype=object
        ),
        names=np.array([record["name"] for record in records], dtype=object),
        parents=np.array([record["parent"] for record in records], dtype=object),
        urls=np.array([record["url"] for record in records], dtype=object),
    )


def _index_from_extracts(records: list[dict[str, Any]]) -> OsmExtractsIndex:
    """Build an :class:`OsmExtractsIndex` via ``from_extracts``.

    This path recalculates ``area`` and ``file_name`` from the geometry and
    parent hierarchy, and repairs invalid geometries (mirrors production
    behaviour).
    """
    from osmfinder._typing import OpenStreetMapExtract

    return OsmExtractsIndex.from_extracts(
        [
            OpenStreetMapExtract(
                id=record["id"],
                name=record["name"],
                parent=record["parent"],
                url=record.get("url", ""),
                geometry=record.get("geometry", box(0, 0, 1, 1)),
            )
            for record in records
        ]
    )
