"""Root pytest configuration to help debug CI test ordering and cache state."""

import logging
import os
import sys
from pathlib import Path

# Configure a simple logger that writes to stderr so it appears in CI logs.
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("osmfinder.ci_debug")


def _log_cache_dir() -> None:
    """Log the cache directory location and contents."""
    try:
        from osmfinder._typing import OsmExtractSource
        from osmfinder.extract import _get_global_cache_file_path

        cache_dir = Path(_get_global_cache_file_path(OsmExtractSource.geofabrik)).parent
        logger.debug("Cache directory: %s", cache_dir)
        if cache_dir.exists():
            files = sorted(cache_dir.iterdir())
            logger.debug("Cache files: %s", [f.name for f in files])
        else:
            logger.debug("Cache directory does not exist yet")
    except Exception as exc:  # pragma: no cover - debug helper
        logger.debug("Could not inspect cache: %s", exc)


_log_cache_dir()


def pytest_sessionfinish(session, exitstatus):  # type: ignore[no-untyped-def]
    """Log cache state after the test session."""
    _log_cache_dir()


def pytest_configure(config):  # type: ignore[no-untyped-def]
    """Log pytest rootdir and testpaths to confirm execution order."""
    is_doctest = "--doctest-modules" in config.invocation_params.args
    logger.debug("CWD: %s", os.getcwd())
    logger.debug("CWD contents: %s", os.listdir(os.getcwd()))
    logger.debug(
        "pytest rootdir=%s testpaths=%s doctest=%s",
        config.rootdir,
        getattr(config, "_testpaths", None),
        is_doctest,
    )
