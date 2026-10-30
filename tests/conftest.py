"""Pytest fixtures providing isolated, synthetic GTFS feeds for unit testing."""

import csv
import sys
from pathlib import Path
import pytest

# Ensure src is on path for tests
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from routeranker.config import PipelineConfig


@pytest.fixture
def synthetic_gtfs_dir(tmp_path: Path) -> Path:
    """Create a minimal valid synthetic GTFS feed for unit testing."""
    gtfs_dir = tmp_path / "synthetic_gtfs"
    gtfs_dir.mkdir()

    # 1. agency.txt
    with open(gtfs_dir / "agency.txt", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["agency_id", "agency_name", "agency_url", "agency_timezone", "agency_lang"])
        writer.writerow(["1", "BMTC", "https://mybmtc.karnataka.gov.in", "Asia/Kolkata", "en"])

    # 2. calendar.txt
    with open(gtfs_dir / "calendar.txt", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "start_date", "end_date"])
        writer.writerow(["1", "1", "1", "1", "1", "1", "1", "1", "20260907", "20270907"])

    # 3. routes.txt (2 urban routes, 1 outstation route)
    with open(gtfs_dir / "routes.txt", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["route_id", "agency_id", "route_short_name", "route_long_name", "route_type"])
        writer.writerow(["R1", "1", "335-E", "Kempegowda Bus Station - Whitefield", "3"])
        writer.writerow(["R2", "1", "500-D", "Hebbal - Central Silk Board", "3"])
        writer.writerow(["R_OUT", "1", "KSRTC-01", "KSRTC-BENGALURU - KALABURAGI", "3"])

    # 4. stops.txt (Central Majestic, Whitefield, Hebbal, Silk Board, and Kalaburagi outstation)
    with open(gtfs_dir / "stops.txt", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["stop_id", "stop_name", "stop_lat", "stop_lon", "zone_id", "location_type"])
        # Majestic (Center)
        writer.writerow(["S1", "Kempegowda Bus Station", "12.9778", "77.5727", "Z1", "1"])
        # MG Road (~4 km east)
        writer.writerow(["S2", "MG Road Metro", "12.9756", "77.6066", "Z1", "0"])
        # Indiranagar (~8 km east)
        writer.writerow(["S3", "Indiranagar 100ft Road", "12.9784", "77.6408", "Z1", "0"])
        # Whitefield (~20 km east)
        writer.writerow(["S4", "Whitefield TTMC", "12.9698", "77.7499", "Z2", "0"])
        # Hebbal (~8 km north)
        writer.writerow(["S5", "Hebbala Bridge", "13.0358", "77.5970", "Z1", "0"])
        # Silk Board (~10 km south)
        writer.writerow(["S6", "Central Silk Board", "12.9176", "77.6233", "Z1", "0"])
        # Outstation stop (~490 km north)
        writer.writerow(["S_OUT", "KSRTC-KALABURAGI BUS STAND", "17.3263", "76.8181", "Z_OUT", "0"])

    # 5. trips.txt
    with open(gtfs_dir / "trips.txt", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["trip_id", "route_id", "service_id", "trip_headsign", "direction_id", "shape_id"])
        writer.writerow(["T1_1", "R1", "1", "Whitefield", "0", "SHP1"])
        writer.writerow(["T1_2", "R1", "1", "Whitefield", "0", "SHP1"])
        writer.writerow(["T2_1", "R2", "1", "Silk Board", "0", "SHP2"])
        writer.writerow(["T_OUT", "R_OUT", "1", "Kalaburagi", "0", "SHP_OUT"])

    # 6. stop_times.txt (T1_1 morning peak, T1_2 morning peak with time > 24h test case for post-midnight test)
    with open(gtfs_dir / "stop_times.txt", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"])
        # T1_1: 08:00 to 08:45
        writer.writerow(["T1_1", "08:00:00", "08:00:00", "S1", "1"])
        writer.writerow(["T1_1", "08:15:00", "08:15:00", "S2", "2"])
        writer.writerow(["T1_1", "08:30:00", "08:30:00", "S3", "3"])
        writer.writerow(["T1_1", "08:45:00", "08:45:00", "S4", "4"])
        # T1_2: 08:20 to 09:05 (20 min headway from T1_1)
        writer.writerow(["T1_2", "08:20:00", "08:20:00", "S1", "1"])
        writer.writerow(["T1_2", "08:35:00", "08:35:00", "S2", "2"])
        writer.writerow(["T1_2", "08:50:00", "08:50:00", "S3", "3"])
        writer.writerow(["T1_2", "09:05:00", "09:05:00", "S4", "4"])
        # T2_1: 09:00 to 09:40
        writer.writerow(["T2_1", "09:00:00", "09:00:00", "S5", "1"])
        writer.writerow(["T2_1", "09:40:00", "09:40:00", "S6", "2"])
        # T_OUT: Outstation trip
        writer.writerow(["T_OUT", "21:00:00", "21:00:00", "S1", "1"])
        writer.writerow(["T_OUT", "25:30:00", "25:30:00", "S_OUT", "2"])  # Note 25:30:00 valid GTFS test!

    # 7. shapes.txt
    with open(gtfs_dir / "shapes.txt", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"])
        # SHP1 (S1 to S4)
        writer.writerow(["SHP1", "12.9778", "77.5727", "1"])
        writer.writerow(["SHP1", "12.9756", "77.6066", "2"])
        writer.writerow(["SHP1", "12.9784", "77.6408", "3"])
        writer.writerow(["SHP1", "12.9698", "77.7499", "4"])
        # SHP2 (S5 to S6)
        writer.writerow(["SHP2", "13.0358", "77.5970", "1"])
        writer.writerow(["SHP2", "12.9176", "77.6233", "2"])
        # SHP_OUT
        writer.writerow(["SHP_OUT", "12.9778", "77.5727", "1"])
        writer.writerow(["SHP_OUT", "17.3263", "76.8181", "2"])

    return gtfs_dir


@pytest.fixture
def test_config(synthetic_gtfs_dir: Path, tmp_path: Path) -> PipelineConfig:
    """Return pipeline configuration bound to the synthetic fixture."""
    return PipelineConfig(
        repo_root=tmp_path,
        gtfs_dir=synthetic_gtfs_dir,
        output_dir=tmp_path / "test_outputs",
        filtering_mode="STRICT_URBAN",
    )
