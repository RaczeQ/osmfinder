"""Shared fixtures and helpers for the osmfinder test suite."""

import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pytest
from shapely.geometry import box

from osmfinder._typing import OsmExtractsIndex

from tests._helpers import _index_from_extracts, _index_from_records  # noqa: E402


@pytest.fixture
def fake_index() -> OsmExtractsIndex:
    """A tiny two-extract index: a big region containing a small nested one."""
    return OsmExtractsIndex(
        ids=np.array(["a", "b"], dtype=object),
        geometries=np.array([box(0, 0, 10, 10), box(0, 0, 2, 2)], dtype=object),
        areas=np.array([100.0, 4.0]),
        file_names=np.array(["big", "big_small"], dtype=object),
        names=np.array(["Big", "Small"], dtype=object),
        parents=np.array(["root", "a"], dtype=object),
        urls=np.array(
            ["http://example.test/big.osm.pbf", "http://example.test/small.osm.pbf"],
            dtype=object,
        ),
    )


@pytest.fixture(scope="session")
def index_from_records() -> Callable[[list[dict[str, Any]]], OsmExtractsIndex]:
    """Session-scoped callable to build an index from records (area/file_name preserved)."""
    return _index_from_records


@pytest.fixture(scope="session")
def index_from_extracts() -> Callable[[list[dict[str, Any]]], OsmExtractsIndex]:
    """Session-scoped callable to build an index via from_extracts (recalc file_names/areas)."""
    return _index_from_extracts
