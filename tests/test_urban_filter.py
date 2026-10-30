"""Unit tests for urban vs outstation service filtering."""

from pathlib import Path
import pytest

from routeranker.config import PipelineConfig
from routeranker.gtfs_loader import GTFSLoader
from routeranker.urban_filter import UrbanFilter, haversine_distance_km


def test_haversine_distance():
    """Verify haversine distance calculation against known geographic baselines."""
    # Majestic to Whitefield: ~20 km
    d = haversine_distance_km(12.9778, 77.5727, 12.9698, 77.7499)
    assert 18.0 <= d <= 21.0

    # Majestic to Kalaburagi: ~490 km
    d_out = haversine_distance_km(12.9778, 77.5727, 17.3263, 76.8181)
    assert 480.0 <= d_out <= 505.0


def test_urban_filtering_classification(test_config: PipelineConfig, synthetic_gtfs_dir: Path):
    """Verify that urban routes are retained and outstation routes are excluded."""
    loader = GTFSLoader(synthetic_gtfs_dir)
    agencies = loader.load_agency()
    routes = loader.load_routes(set(agencies.keys()))
    stops = loader.load_stops()
    trips = loader.load_trips(set(routes.keys()), set())

    # Build trip_stop_ids from fixture
    trip_stop_ids = {
        "T1_1": {"S1", "S2", "S3", "S4"},
        "T1_2": {"S1", "S2", "S3", "S4"},
        "T2_1": {"S5", "S6"},
        "T_OUT": {"S1", "S_OUT"},
    }

    urban_filter = UrbanFilter(test_config)
    stop_metrics = urban_filter.evaluate_stops(stops)
    decisions = urban_filter.classify_routes(routes, trips, trip_stop_ids, stop_metrics)

    assert decisions["R1"].classification == "URBAN"
    assert decisions["R2"].classification == "URBAN"
    assert decisions["R_OUT"].classification == "OUTSTATION"
    assert decisions["R_OUT"].has_outstation_keyword is True

    retained_routes, retained_trips, _, summary = urban_filter.apply_filter(
        routes, trips, stops, decisions
    )

    assert "R1" in retained_routes
    assert "R2" in retained_routes
    assert "R_OUT" not in retained_routes

    assert "T1_1" in retained_trips
    assert "T1_2" in retained_trips
    assert "T2_1" in retained_trips
    assert "T_OUT" not in retained_trips

    assert summary.routes_retained == 2
    assert summary.routes_excluded == 1
    assert summary.trips_retained == 3
    assert summary.trips_excluded == 1


def test_filtering_modes(test_config: PipelineConfig, synthetic_gtfs_dir: Path):
    """Verify that filtering mode 'ALL' preserves outstation routes."""
    test_config.filtering_mode = "ALL"
    loader = GTFSLoader(synthetic_gtfs_dir)
    agencies = loader.load_agency()
    routes = loader.load_routes(set(agencies.keys()))
    stops = loader.load_stops()
    trips = loader.load_trips(set(routes.keys()), set())

    trip_stop_ids = {
        "T1_1": {"S1", "S2", "S3", "S4"},
        "T1_2": {"S1", "S2", "S3", "S4"},
        "T2_1": {"S5", "S6"},
        "T_OUT": {"S1", "S_OUT"},
    }

    urban_filter = UrbanFilter(test_config)
    stop_metrics = urban_filter.evaluate_stops(stops)
    decisions = urban_filter.classify_routes(routes, trips, trip_stop_ids, stop_metrics)
    retained_routes, retained_trips, _, summary = urban_filter.apply_filter(
        routes, trips, stops, decisions
    )

    assert len(retained_routes) == 3
    assert "R_OUT" in retained_routes
    assert len(retained_trips) == 4
