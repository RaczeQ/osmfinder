"""Tests for spherical area calculation."""

import geopandas as gpd
import numpy as np
import pytest
from shapely import GeometryCollection, LineString, MultiPolygon, Point, box

from osmfinder._area_helper import calculate_spherical_area


def test_polygons_nested_in_geometry_collections() -> None:
    """Test if polygons are counted at any nesting level and other geometries are ignored."""
    multipolygon = MultiPolygon([box(0, 0, 1, 1), box(2, 0, 3, 1)])
    # make_valid returns a collection like this for polygons with spikes
    nested = GeometryCollection([multipolygon, LineString([(5, 5), (5, 6)])])
    deeply_nested = GeometryCollection([GeometryCollection([nested]), Point(9, 9)])

    expected = calculate_spherical_area(multipolygon)
    assert expected > 0
    assert calculate_spherical_area(nested) == pytest.approx(expected)

    areas = calculate_spherical_area([nested, Point(0, 0), box(0, 0, 1, 1), deeply_nested])
    np.testing.assert_allclose(areas, [expected, 0, expected / 2, expected])


@pytest.mark.parametrize("geometry", [box(0, 0, 1, 1), MultiPolygon([box(0, 0, 1, 1)])])
def test_geoseries_and_read_only_array(geometry: object) -> None:
    """Test if a GeoSeries and a read-only array are accepted."""
    expected = [calculate_spherical_area(geometry)]

    np.testing.assert_allclose(calculate_spherical_area(gpd.GeoSeries([geometry])), expected)

    array = np.array([geometry], dtype=object)
    array.flags.writeable = False
    np.testing.assert_allclose(calculate_spherical_area(array), expected)
