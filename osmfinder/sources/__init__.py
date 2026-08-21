"""OpenStreetMap `*.osm.pbf` extract sources (Geofabrik, BBBike, OSM.fr, Movisda, GEO2day)."""

import importlib.util
import sys
from pathlib import Path

# Import all OSM sources extracts logic dynamically
_sources_package = Path(__file__).parent
for _source_file in _sources_package.glob("*.py"):
    if _source_file.name in ("__init__.py", "tree.py"):
        continue
    _module_name = f"osmfinder.sources.{_source_file.stem}"
    if _module_name not in sys.modules:
        _spec = importlib.util.spec_from_file_location(_module_name, _source_file)
        if _spec is not None and _spec.loader is not None:
            _module = importlib.util.module_from_spec(_spec)
            sys.modules[_module_name] = _module
            _spec.loader.exec_module(_module)
            setattr(sys.modules[__name__], _source_file.stem, _module)
