"""
Small compatibility helpers.

These were previously imported from ``rq_geo_toolkit``. They are vendored here (a few lines each)
so that ``osmfinder`` stays lightweight and doesn't pull the whole toolkit as a dependency.
"""

import os

# Force terminal-style (non-jupyter, non-interactive) rich output when set.
FORCE_TERMINAL = os.getenv("FORCE_TERMINAL_MODE", "false").lower() == "true"

__all__ = [
    "FORCE_TERMINAL",
]