"""Shared fixtures and helpers for the osmfinder test suite."""

import shutil
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pytest
from pytest_mock import MockerFixture
from shapely.geometry import box
from unittest.mock import patch

from osmfinder._typing import OsmExtractsIndex

from tests._helpers import _index_from_extracts, _index_from_records  # noqa: E402


def _copy_precalculated_indexes_to_cache() -> None:
    """Copy the repo's precalculated parquet indexes into the user cache directory.

    This ensures tests use the local versions (which include all required columns)
    instead of potentially stale downloads from GitHub.
    """
    cache_dir = Path.home() / ".cache" / "osmfinder"
    cache_dir.mkdir(parents=True, exist_ok=True)

    repo_index_dir = Path(__file__).parent.parent / "precalculated_indexes"
    for src in repo_index_dir.glob("*.parquet"):
        dst = cache_dir / src.name
        shutil.copy2(src, dst)


_copy_precalculated_indexes_to_cache()


def _mock_download_precalculated_index_from_github(destination_path: Path) -> bool:
    """Copy a local precalculated index instead of downloading from GitHub."""
    repo_index_dir = Path(__file__).parent.parent / "precalculated_indexes"
    src = repo_index_dir / destination_path.name
    if src.exists():
        shutil.copy2(src, destination_path)
        return True
    return False


# Globally mock the GitHub download to use local files instead.
patch(
    "osmfinder.extract._download_precalculated_index_from_github",
    side_effect=_mock_download_precalculated_index_from_github,
).start()


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
