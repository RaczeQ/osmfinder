"""
Small compatibility helpers.

These were previously imported from ``rq_geo_toolkit``. They are vendored here (a few lines each)
so that ``osmfinder`` stays lightweight and doesn't pull the whole toolkit as a dependency.
"""

import os

import geopandas as gpd
from packaging import version

# Force terminal-style (non-jupyter, non-interactive) rich output when set.
FORCE_TERMINAL = os.getenv("FORCE_TERMINAL_MODE", "false").lower() == "true"

# GeoPandas 1.0 renamed/added some APIs (e.g. ``union_all`` vs ``unary_union``).
GEOPANDAS_NEW_API = version.parse(gpd.__version__) >= version.parse("1.0.0")

__all__ = [
    "FORCE_TERMINAL",
    "GEOPANDAS_NEW_API",
]
