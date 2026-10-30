"""Unit tests for GTFS loader, schema validation, and time parsing."""

import csv
from pathlib import Path
import pytest

from routeranker.gtfs_loader import GTFSLoader, format_gtfs_time, parse_gtfs_time


def test_gtfs_time_parsing():
    """Verify GTFS time parsing for standard, midnight, and post-midnight (>24h) timestamps."""
    # Standard times
    assert parse_gtfs_time("00:00:00") == 0
    assert parse_gtfs_time("08:30:15") == 8 * 3600 + 30 * 60 + 15
    assert parse_gtfs_time("23:59:59") == 86399

    # Valid GTFS times spanning past midnight (> 24:00:00)
    assert parse_gtfs_time("24:00:00") == 86400
    assert parse_gtfs_time("25:30:00") == 25 * 3600 + 30 * 60
    assert parse_gtfs_time("27:15:30") == 27 * 3600 + 15 * 60 + 30

    # Invalid time formats
    assert parse_gtfs_time("") is None
    assert parse_gtfs_time("invalid") is None
    assert parse_gtfs_time("12:65:00") is None  # minutes >= 60
    assert parse_gtfs_time("12:00:60") is None  # seconds >= 60
    assert parse_gtfs_time("-01:00:00") is None

    # Formatting round-trip
    assert format_gtfs_time(25 * 3600 + 30 * 60) == "25:30:00"


def test_missing_required_files(tmp_path: Path):
    """Verify that missing required GTFS files raise FileNotFoundError."""
    empty_dir = tmp_path / "empty_gtfs"
    empty_dir.mkdir()

    loader = GTFSLoader(empty_dir)
    with pytest.raises(FileNotFoundError, match="Missing mandatory GTFS files"):
        loader.validate_file_existence()


def test_missing_required_columns(tmp_path: Path):
    """Verify that missing required header columns are flagged as validation errors."""
    gtfs_dir = tmp_path / "bad_headers"
    gtfs_dir.mkdir()

    # Create dummy agency without agency_timezone
    with open(gtfs_dir / "agency.txt", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["agency_id", "agency_name"])  # Missing agency_url and agency_timezone
        writer.writerow(["1", "BMTC"])

    loader = GTFSLoader(gtfs_dir)
    agencies = loader.load_agency()
    assert len(agencies) == 0
    assert loader.report.error_count() > 0
    assert any("Missing required columns in agency.txt" in i.message for i in loader.report.issues)


def test_duplicate_primary_keys(synthetic_gtfs_dir: Path, tmp_path: Path):
    """Verify detection of duplicate route_id and stop_id."""
    # Append duplicate route to routes.txt
    routes_file = synthetic_gtfs_dir / "routes.txt"
    with open(routes_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["R1", "1", "Duplicate Route", "Duplicate Long", "3"])

    loader = GTFSLoader(synthetic_gtfs_dir)
    agencies = loader.load_agency()
    routes = loader.load_routes(set(agencies.keys()))

    assert any(i.severity == "ERROR" and "Duplicate route_id: R1" in i.message for i in loader.report.issues)


def test_invalid_coordinates(synthetic_gtfs_dir: Path):
    """Verify that stops with invalid coordinates (out of bounds or non-numeric) are rejected."""
    stops_file = synthetic_gtfs_dir / "stops.txt"
    with open(stops_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["BAD_COORD_1", "Out of Bounds Stop", "95.0", "77.0", "Z", "0"])  # Lat > 90
        writer.writerow(["BAD_COORD_2", "Non numeric stop", "INVALID", "77.0", "Z", "0"])

    loader = GTFSLoader(synthetic_gtfs_dir)
    stops = loader.load_stops()

    assert "BAD_COORD_1" not in stops
    assert "BAD_COORD_2" not in stops
    assert any("Invalid coordinates" in i.message for i in loader.report.issues)


def test_broken_foreign_keys(synthetic_gtfs_dir: Path):
    """Verify that trips referencing non-existent routes or services are flagged."""
    trips_file = synthetic_gtfs_dir / "trips.txt"
    with open(trips_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["T_BROKEN", "NON_EXISTENT_ROUTE", "1", "Headsign", "0", "SHP1"])

    loader = GTFSLoader(synthetic_gtfs_dir)
    agencies = loader.load_agency()
    routes = loader.load_routes(set(agencies.keys()))
    calendar = loader.load_calendar()
    trips = loader.load_trips(set(routes.keys()), set(calendar.keys()))

    assert any("unknown route_id: NON_EXISTENT_ROUTE" in i.message for i in loader.report.issues)


def test_stop_sequence_inversion(synthetic_gtfs_dir: Path):
    """Verify that non-increasing stop sequences in a trip are flagged as errors."""
    st_file = synthetic_gtfs_dir / "stop_times.txt"
    with open(st_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        # Sequence inversions (seq 5 followed by seq 4)
        writer.writerow(["T_INV", "10:00:00", "10:00:00", "S1", "5"])
        writer.writerow(["T_INV", "10:15:00", "10:15:00", "S2", "4"])

    loader = GTFSLoader(synthetic_gtfs_dir)
    # Stream stop times
    stop_times = list(loader.stream_stop_times(trip_ids={"T_INV"}, stop_ids={"S1", "S2"}))
    assert any("Sequence non-increasing" in i.message for i in loader.report.issues)
