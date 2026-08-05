"""Constants used across osmfinder."""

WGS84_CRS = "EPSG:4326"

OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS = 30

# Sent as the User-Agent header on every request to the extract providers.
USER_AGENT = "osmfinder Python package (https://github.com/RaczeQ/osmfinder)"

__all__ = [
    "OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS",
    "USER_AGENT",
    "WGS84_CRS",
]
