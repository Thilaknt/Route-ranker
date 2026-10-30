"""Data models and schema definitions for RouteRanker GTFS entities and validation reports."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


@dataclass(frozen=True)
class Agency:
    agency_id: str
    agency_name: str
    agency_url: str
    agency_timezone: str
    agency_lang: Optional[str] = None


@dataclass(frozen=True)
class Route:
    route_id: str
    agency_id: str
    route_short_name: str
    route_long_name: str
    route_type: int


@dataclass(frozen=True)
class Stop:
    stop_id: str
    stop_name: str
    stop_lat: float
    stop_lon: float
    stop_desc: Optional[str] = None
    zone_id: Optional[str] = None
    location_type: str = ""
    parent_station: Optional[str] = None
    platform_code: Optional[str] = None


@dataclass(frozen=True)
class Trip:
    trip_id: str
    route_id: str
    service_id: str
    trip_headsign: Optional[str] = None
    direction_id: Optional[str] = None
    shape_id: Optional[str] = None


@dataclass(frozen=True)
class StopTime:
    trip_id: str
    arrival_time_sec: int  # Seconds since midnight (can be >= 86400)
    departure_time_sec: int
    stop_id: str
    stop_sequence: int
    stop_headsign: Optional[str] = None


@dataclass(frozen=True)
class ShapePoint:
    shape_id: str
    lat: float
    lon: float
    sequence: int
    dist_traveled: Optional[float] = None


@dataclass(frozen=True)
class CalendarService:
    service_id: str
    monday: int
    tuesday: int
    wednesday: int
    thursday: int
    friday: int
    saturday: int
    sunday: int
    start_date: str
    end_date: str


@dataclass
class ValidationIssue:
    severity: str  # 'ERROR' or 'WARNING'
    table: str
    record_id: Optional[str]
    field: Optional[str]
    message: str


@dataclass
class DataQualityReport:
    is_valid: bool
    total_agencies: int = 0
    total_routes: int = 0
    total_trips: int = 0
    total_stops: int = 0
    total_stop_times: int = 0
    total_shape_points: int = 0
    total_shapes: int = 0
    total_calendar_services: int = 0
    calendar_dates_present: bool = False
    frequencies_present: bool = False
    routes_without_trips: int = 0
    trips_without_stop_times: int = 0
    issues: List[ValidationIssue] = field(default_factory=list)

    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "ERROR")

    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "WARNING")


@dataclass
class FilteringDecision:
    route_id: str
    route_short_name: str
    route_long_name: str
    classification: str  # 'URBAN', 'SUBURBAN', 'OUTSTATION'
    max_stop_distance_km: float
    has_outstation_keyword: bool
    reason: str
    trip_count: int


@dataclass
class UrbanFilteringSummary:
    mode: str
    total_routes_before: int
    total_trips_before: int
    total_stops_before: int
    routes_retained: int
    trips_retained: int
    stops_retained: int
    routes_excluded: int
    trips_excluded: int
    stops_excluded: int
    exclusion_breakdown: Dict[str, int] = field(default_factory=dict)
    borderline_routes: List[FilteringDecision] = field(default_factory=list)
