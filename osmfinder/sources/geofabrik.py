"""
Geofabrik OpenStreetMap extracts.

This module contains wrapper for publically available Geofabrik download server.
"""

import json
import operator
from typing import Any

from shapely.geometry import shape

from osmfinder._constants import OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS, USER_AGENT
from osmfinder._network import get_with_retries
from osmfinder._typing import OpenStreetMapExtract, OsmExtractsIndex, OsmExtractSource
from osmfinder.extract import load_index_decorator

GEOFABRIK_INDEX_URL = "https://download.geofabrik.de/index-v1.json"


@load_index_decorator(OsmExtractSource.geofabrik, fast_build=True)
def _load_geofabrik_index(**kwargs: Any) -> OsmExtractsIndex:  # pragma: no cover
    """
    Load available extracts from GeoFabrik download service.

    Returns:
        OsmExtractsIndex: Extracts index with metadata.
    """
    result = get_with_retries(
        GEOFABRIK_INDEX_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS,
    )
    parsed_data = json.loads(result.text)
    extracts = _parse_geofabrik_index(parsed_data)
    return OsmExtractsIndex.from_extracts(extracts)


def _parse_geofabrik_index(parsed_data: dict[str, Any]) -> list[OpenStreetMapExtract]:
    """
    Parse a Geofabrik `index-v1.json` payload into a list of extracts.

    Args:
        parsed_data (dict[str, Any]): Parsed Geofabrik index JSON.

    Returns:
        list[OpenStreetMapExtract]: List of parsed extracts.
    """
    geofabrik_enum_value = OsmExtractSource.geofabrik.value

    extracts = []
    for feature in parsed_data["features"]:
        properties = feature["properties"]
        raw_id = properties["id"]
        raw_parent = properties.get("parent")

        extract_id = f"{geofabrik_enum_value}_{raw_id}"
        name = raw_id.replace("/", "_")
        parent = (
            f"{geofabrik_enum_value}_{raw_parent}"
            if raw_parent is not None
            else geofabrik_enum_value
        )
        url = operator.itemgetter("pbf")(properties["urls"])
        geometry = shape(feature["geometry"])

        # fix US extracts parent tree
        if extract_id.startswith(f"{geofabrik_enum_value}_us/"):
            parent = f"{geofabrik_enum_value}_us"

        extracts.append(
            OpenStreetMapExtract(
                id=extract_id,
                name=name,
                parent=parent,
                url=url,
                geometry=geometry,
            )
        )

    return extracts
