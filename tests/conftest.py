"""Shared fixtures and helpers for the osmfinder test suite."""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from shapely.geometry import box

from osmfinder._typing import OsmExtractsIndex


def _mock_download_precalculated_index_from_github(destination_path: Path) -> bool:
    """Copy a local precalculated index instead of downloading from GitHub."""
    repo_index_dir = Path(__file__).parent.parent / "precalculated_indexes"
    src = repo_index_dir / destination_path.name
    if src.exists():
        import shutil

        shutil.copy2(src, destination_path)
        return True
    return False


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
