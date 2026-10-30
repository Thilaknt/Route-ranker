"""GTFS loader module with streaming validation, integrity checking, and memory-efficient ingestion."""

import csv
import logging
import math
from pathlib import Path
from typing import Dict, Generator, Iterator, List, Optional, Set, Tuple

from routeranker.schemas import (
    Agency,
    CalendarService,
    DataQualityReport,
    Route,
    ShapePoint,
    Stop,
    StopTime,
    Trip,
    ValidationIssue,
)

logger = logging.getLogger(__name__)


def parse_gtfs_time(time_str: str) -> Optional[int]:
    """Parse GTFS time string 'HH:MM:SS' into seconds since midnight.

    Supports hours >= 24 as permitted by the GTFS specification for trips spanning midnight.
    Returns None if format is invalid.
    """
    if not time_str:
        return None
    parts = time_str.strip().split(":")
    if len(parts) != 3:
        return None
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = int(parts[2])
        if hours < 0 or minutes < 0 or minutes >= 60 or seconds < 0 or seconds >= 60:
            return None
        return hours * 3600 + minutes * 60 + seconds
    except (ValueError, TypeError):
        return None


def format_gtfs_time(seconds_since_midnight: int) -> str:
    """Format seconds since midnight back to GTFS 'HH:MM:SS'."""
    hours = seconds_since_midnight // 3600
    rem = seconds_since_midnight % 3600
    minutes = rem // 60
    seconds = rem % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


class GTFSLoader:
    """Loads and validates GTFS datasets with referential integrity checks and streaming iteration."""

    REQUIRED_FILES = [
        "agency.txt",
        "routes.txt",
        "trips.txt",
        "stops.txt",
        "stop_times.txt",
    ]

    REQUIRED_COLUMNS = {
        "agency.txt": ["agency_name", "agency_url", "agency_timezone"],
        "routes.txt": ["route_id", "route_type"],
        "trips.txt": ["route_id", "service_id", "trip_id"],
        "stops.txt": ["stop_id", "stop_name", "stop_lat", "stop_lon"],
        "stop_times.txt": ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
    }

    def __init__(self, gtfs_dir: Path):
        self.gtfs_dir = Path(gtfs_dir)
        self.report = DataQualityReport(is_valid=True)

    def validate_file_existence(self) -> None:
        """Check presence of required GTFS files; raise FileNotFoundError if missing."""
        if not self.gtfs_dir.exists():
            raise FileNotFoundError(f"GTFS directory does not exist: {self.gtfs_dir}")

        missing = []
        for fname in self.REQUIRED_FILES:
            fp = self.gtfs_dir / fname
            if not fp.is_file():
                missing.append(fname)

        # Check calendar or calendar_dates
        has_cal = (self.gtfs_dir / "calendar.txt").is_file()
        has_cal_dates = (self.gtfs_dir / "calendar_dates.txt").is_file()
        if not has_cal and not has_cal_dates:
            missing.append("calendar.txt or calendar_dates.txt")

        if missing:
            msg = f"Missing mandatory GTFS files in {self.gtfs_dir}: {', '.join(missing)}"
            self.report.issues.append(
                ValidationIssue(severity="ERROR", table="feed", record_id=None, field=None, message=msg)
            )
            self.report.is_valid = False
            raise FileNotFoundError(msg)

        # Check optional files
        self.report.calendar_dates_present = has_cal_dates
        self.report.frequencies_present = (self.gtfs_dir / "frequencies.txt").is_file()

    def _check_header(self, fname: str, actual_fields: List[str]) -> bool:
        """Validate that all required columns for a table exist in actual header fields."""
        required = self.REQUIRED_COLUMNS.get(fname, [])
        missing_cols = [c for c in required if c not in actual_fields]
        if missing_cols:
            self.report.issues.append(
                ValidationIssue(
                    severity="ERROR",
                    table=fname,
                    record_id=None,
                    field="header",
                    message=f"Missing required columns in {fname}: {missing_cols}",
                )
            )
            self.report.is_valid = False
            return False
        return True

    def load_agency(self) -> Dict[str, Agency]:
        """Load and validate agency.txt."""
        agency_file = self.gtfs_dir / "agency.txt"
        agencies: Dict[str, Agency] = {}
        with open(agency_file, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if not self._check_header("agency.txt", reader.fieldnames or []):
                return agencies

            for row in reader:
                aid = row.get("agency_id", "").strip() or "DEFAULT_AGENCY"
                if aid in agencies:
                    self.report.issues.append(
                        ValidationIssue("WARNING", "agency.txt", aid, "agency_id", f"Duplicate agency_id: {aid}")
                    )
                agencies[aid] = Agency(
                    agency_id=aid,
                    agency_name=row.get("agency_name", "").strip(),
                    agency_url=row.get("agency_url", "").strip(),
                    agency_timezone=row.get("agency_timezone", "").strip(),
                    agency_lang=row.get("agency_lang", "").strip() or None,
                )

        self.report.total_agencies = len(agencies)
        return agencies

    def load_calendar(self) -> Dict[str, CalendarService]:
        """Load and validate calendar.txt."""
        cal_file = self.gtfs_dir / "calendar.txt"
        services: Dict[str, CalendarService] = {}
        if not cal_file.is_file():
            return services

        with open(cal_file, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sid = row.get("service_id", "").strip()
                if not sid:
                    continue
                if sid in services:
                    self.report.issues.append(
                        ValidationIssue("WARNING", "calendar.txt", sid, "service_id", f"Duplicate service_id: {sid}")
                    )
                try:
                    services[sid] = CalendarService(
                        service_id=sid,
                        monday=int(row.get("monday", 0)),
                        tuesday=int(row.get("tuesday", 0)),
                        wednesday=int(row.get("wednesday", 0)),
                        thursday=int(row.get("thursday", 0)),
                        friday=int(row.get("friday", 0)),
                        saturday=int(row.get("saturday", 0)),
                        sunday=int(row.get("sunday", 0)),
                        start_date=row.get("start_date", "").strip(),
                        end_date=row.get("end_date", "").strip(),
                    )
                except ValueError as e:
                    self.report.issues.append(
                        ValidationIssue("ERROR", "calendar.txt", sid, None, f"Malformed calendar row: {e}")
                    )

        self.report.total_calendar_services = len(services)
        return services

    def load_routes(self, agency_ids: Set[str]) -> Dict[str, Route]:
        """Load and validate routes.txt, checking agency FK."""
        routes_file = self.gtfs_dir / "routes.txt"
        routes: Dict[str, Route] = {}
        with open(routes_file, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if not self._check_header("routes.txt", reader.fieldnames or []):
                return routes

            for row in reader:
                rid = row.get("route_id", "").strip()
                if not rid:
                    self.report.issues.append(
                        ValidationIssue("ERROR", "routes.txt", None, "route_id", "Empty route_id encountered")
                    )
                    continue

                if rid in routes:
                    self.report.issues.append(
                        ValidationIssue("ERROR", "routes.txt", rid, "route_id", f"Duplicate route_id: {rid}")
                    )
                    self.report.is_valid = False
                    continue

                aid = row.get("agency_id", "").strip()
                if aid and aid not in agency_ids and agency_ids:
                    self.report.issues.append(
                        ValidationIssue(
                            "WARNING",
                            "routes.txt",
                            rid,
                            "agency_id",
                            f"Route {rid} references unknown agency_id: {aid}",
                        )
                    )

                sname = row.get("route_short_name", "").strip()
                lname = row.get("route_long_name", "").strip()
                if not sname and not lname:
                    self.report.issues.append(
                        ValidationIssue(
                            "WARNING",
                            "routes.txt",
                            rid,
                            "route_names",
                            f"Route {rid} lacks both short and long names",
                        )
                    )

                try:
                    rtype = int(row.get("route_type", 3))
                except ValueError:
                    rtype = 3

                routes[rid] = Route(
                    route_id=rid,
                    agency_id=aid,
                    route_short_name=sname,
                    route_long_name=lname,
                    route_type=rtype,
                )

        self.report.total_routes = len(routes)
        return routes

    def load_stops(self) -> Dict[str, Stop]:
        """Load and validate stops.txt, checking coordinates and parent station hierarchy."""
        stops_file = self.gtfs_dir / "stops.txt"
        stops: Dict[str, Stop] = {}
        with open(stops_file, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if not self._check_header("stops.txt", reader.fieldnames or []):
                return stops

            for row in reader:
                sid = row.get("stop_id", "").strip()
                if not sid:
                    self.report.issues.append(
                        ValidationIssue("ERROR", "stops.txt", None, "stop_id", "Empty stop_id encountered")
                    )
                    continue

                if sid in stops:
                    self.report.issues.append(
                        ValidationIssue("ERROR", "stops.txt", sid, "stop_id", f"Duplicate stop_id: {sid}")
                    )
                    self.report.is_valid = False
                    continue

                lat_str = row.get("stop_lat", "").strip()
                lon_str = row.get("stop_lon", "").strip()
                try:
                    lat = float(lat_str)
                    lon = float(lon_str)
                    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
                        raise ValueError(f"Out of range coordinates: ({lat}, {lon})")
                except (ValueError, TypeError) as e:
                    self.report.issues.append(
                        ValidationIssue(
                            "ERROR", "stops.txt", sid, "coordinates", f"Invalid coordinates '{lat_str}, {lon_str}': {e}"
                        )
                    )
                    self.report.is_valid = False
                    continue

                sname = row.get("stop_name", "").strip()
                if not sname:
                    self.report.issues.append(
                        ValidationIssue("WARNING", "stops.txt", sid, "stop_name", "Empty stop_name")
                    )

                stops[sid] = Stop(
                    stop_id=sid,
                    stop_name=sname,
                    stop_lat=lat,
                    stop_lon=lon,
                    stop_desc=row.get("stop_desc", "").strip() or None,
                    zone_id=row.get("zone_id", "").strip() or None,
                    location_type=row.get("location_type", "").strip(),
                    parent_station=row.get("parent_station", "").strip() or None,
                    platform_code=row.get("platform_code", "").strip() or None,
                )

        # Validate parent station foreign keys
        for sid, s in stops.items():
            if s.parent_station and s.parent_station not in stops:
                self.report.issues.append(
                    ValidationIssue(
                        "WARNING",
                        "stops.txt",
                        sid,
                        "parent_station",
                        f"Unknown parent_station {s.parent_station} for stop {sid}",
                    )
                )

        self.report.total_stops = len(stops)
        return stops

    def load_trips(self, route_ids: Set[str], service_ids: Set[str]) -> Dict[str, Trip]:
        """Load and validate trips.txt, checking route and service foreign keys."""
        trips_file = self.gtfs_dir / "trips.txt"
        trips: Dict[str, Trip] = {}
        with open(trips_file, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if not self._check_header("trips.txt", reader.fieldnames or []):
                return trips

            for row in reader:
                tid = row.get("trip_id", "").strip()
                if not tid:
                    self.report.issues.append(
                        ValidationIssue("ERROR", "trips.txt", None, "trip_id", "Empty trip_id encountered")
                    )
                    continue

                if tid in trips:
                    self.report.issues.append(
                        ValidationIssue("ERROR", "trips.txt", tid, "trip_id", f"Duplicate trip_id: {tid}")
                    )
                    self.report.is_valid = False
                    continue

                rid = row.get("route_id", "").strip()
                if rid not in route_ids:
                    self.report.issues.append(
                        ValidationIssue("ERROR", "trips.txt", tid, "route_id", f"Trip references unknown route_id: {rid}")
                    )
                    self.report.is_valid = False

                sid = row.get("service_id", "").strip()
                if service_ids and sid not in service_ids:
                    self.report.issues.append(
                        ValidationIssue("ERROR", "trips.txt", tid, "service_id", f"Trip references unknown service_id: {sid}")
                    )
                    self.report.is_valid = False

                trips[tid] = Trip(
                    trip_id=tid,
                    route_id=rid,
                    service_id=sid,
                    trip_headsign=row.get("trip_headsign", "").strip() or None,
                    direction_id=row.get("direction_id", "").strip() or None,
                    shape_id=row.get("shape_id", "").strip() or None,
                )

        # Detect routes without trips
        referenced_routes = {t.route_id for t in trips.values()}
        unused_routes = route_ids - referenced_routes
        self.report.routes_without_trips = len(unused_routes)
        if unused_routes:
            self.report.issues.append(
                ValidationIssue(
                    "WARNING",
                    "routes.txt",
                    None,
                    "trip_count",
                    f"{len(unused_routes)} routes have 0 trips in trips.txt",
                )
            )

        self.report.total_trips = len(trips)
        return trips

    def load_shapes_summary(self) -> Dict[str, List[Tuple[float, float, int]]]:
        """Stream shapes.txt to compute distinct shape geometries and vertex counts without storing raw text."""
        shapes_file = self.gtfs_dir / "shapes.txt"
        shapes: Dict[str, List[Tuple[float, float, int]]] = {}
        if not shapes_file.is_file():
            return shapes

        total_pts = 0
        with open(shapes_file, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                total_pts += 1
                shp_id = row.get("shape_id", "").strip()
                if not shp_id:
                    continue
                try:
                    lat = float(row.get("shape_pt_lat", 0.0))
                    lon = float(row.get("shape_pt_lon", 0.0))
                    seq = int(row.get("shape_pt_sequence", 0))
                except (ValueError, TypeError):
                    continue

                if shp_id not in shapes:
                    shapes[shp_id] = []
                shapes[shp_id].append((lat, lon, seq))

        # Sort points by sequence
        for shp_id in shapes:
            shapes[shp_id].sort(key=lambda pt: pt[2])

        self.report.total_shape_points = total_pts
        self.report.total_shapes = len(shapes)
        return shapes

    def stream_stop_times(
        self, trip_ids: Set[str], stop_ids: Set[str]
    ) -> Generator[StopTime, None, None]:
        """Stream stop_times.txt one record at a time with strict validation.

        Yields valid StopTime records. Avoids loading 1.54M records in duplicate memory buffers.
        """
        st_file = self.gtfs_dir / "stop_times.txt"
        with open(st_file, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if not self._check_header("stop_times.txt", reader.fieldnames or []):
                return

            last_trip_id = None
            last_seq = -1
            total_records = 0
            observed_trips = set()

            for row in reader:
                total_records += 1
                tid = row.get("trip_id", "").strip()
                sid = row.get("stop_id", "").strip()
                arr_str = row.get("arrival_time", "").strip()
                dep_str = row.get("departure_time", "").strip()
                seq_str = row.get("stop_sequence", "").strip()

                if tid not in trip_ids:
                    self.report.issues.append(
                        ValidationIssue("ERROR", "stop_times.txt", tid, "trip_id", f"Unknown trip_id: {tid}")
                    )
                    self.report.is_valid = False
                    continue

                observed_trips.add(tid)

                if sid not in stop_ids:
                    self.report.issues.append(
                        ValidationIssue("ERROR", "stop_times.txt", tid, "stop_id", f"Unknown stop_id: {sid}")
                    )
                    self.report.is_valid = False
                    continue

                try:
                    seq = int(seq_str)
                except (ValueError, TypeError):
                    self.report.issues.append(
                        ValidationIssue("ERROR", "stop_times.txt", tid, "stop_sequence", f"Invalid stop_sequence: {seq_str}")
                    )
                    self.report.is_valid = False
                    continue

                arr_sec = parse_gtfs_time(arr_str)
                dep_sec = parse_gtfs_time(dep_str)
                if arr_sec is None or dep_sec is None:
                    self.report.issues.append(
                        ValidationIssue(
                            "ERROR",
                            "stop_times.txt",
                            tid,
                            "times",
                            f"Invalid time format in trip {tid}: arr={arr_str}, dep={dep_str}",
                        )
                    )
                    self.report.is_valid = False
                    continue

                # Check sequence monotonicity within same trip
                if tid == last_trip_id:
                    if seq <= last_seq:
                        self.report.issues.append(
                            ValidationIssue(
                                "ERROR",
                                "stop_times.txt",
                                tid,
                                "stop_sequence",
                                f"Sequence non-increasing in trip {tid}: {seq} <= {last_seq}",
                            )
                        )
                        self.report.is_valid = False
                    last_seq = seq
                else:
                    last_trip_id = tid
                    last_seq = seq

                yield StopTime(
                    trip_id=tid,
                    arrival_time_sec=arr_sec,
                    departure_time_sec=dep_sec,
                    stop_id=sid,
                    stop_sequence=seq,
                    stop_headsign=row.get("stop_headsign", "").strip() or None,
                )

            self.report.total_stop_times = total_records
            unserved_trips = len(trip_ids - observed_trips)
            self.report.trips_without_stop_times = unserved_trips
            if unserved_trips > 0:
                self.report.issues.append(
                    ValidationIssue(
                        "WARNING",
                        "trips.txt",
                        None,
                        "stop_times",
                        f"{unserved_trips} trips in trips.txt have 0 stop_times records",
                    )
                )
