"""Geospatial filtering module to isolate Bengaluru urban transit operations from outstation/inter-city services."""

import logging
import math
from typing import Dict, Iterable, List, Optional, Set, Tuple

from routeranker.config import PipelineConfig
from routeranker.schemas import (
    FilteringDecision,
    Route,
    Stop,
    Trip,
    UrbanFilteringSummary,
)

logger = logging.getLogger(__name__)


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two points on the Earth's surface in kilometers."""
    r_earth_km = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r_earth_km * c


class UrbanFilter:
    """Classifies and partitions GTFS transit services into urban, suburban, and outstation categories.

    Uses a multi-evidence approach combining geographic distance from Bengaluru center and operator naming markers.
    Preserves all original source records in memory and returns filtered views deterministically.
    """

    def __init__(self, config: PipelineConfig):
        self.config = config

    def evaluate_stops(self, stops: Dict[str, Stop]) -> Dict[str, Tuple[float, bool]]:
        """Calculate distance from city center and outstation keyword presence for all stops.

        Returns dict mapping stop_id -> (distance_km, has_outstation_keyword).
        """
        center_lat = self.config.bengaluru_center_lat
        center_lon = self.config.bengaluru_center_lon
        keywords = tuple(kw.upper() for kw in self.config.outstation_keywords)

        stop_metrics: Dict[str, Tuple[float, bool]] = {}
        for sid, s in stops.items():
            dist = haversine_distance_km(center_lat, center_lon, s.stop_lat, s.stop_lon)
            has_kw = any(kw in s.stop_name.upper() for kw in keywords)
            stop_metrics[sid] = (dist, has_kw)

        return stop_metrics

    def classify_routes(
        self,
        routes: Dict[str, Route],
        trips: Dict[str, Trip],
        trip_stop_ids: Dict[str, Set[str]],
        stop_metrics: Dict[str, Tuple[float, bool]],
    ) -> Dict[str, FilteringDecision]:
        """Classify each route into URBAN, SUBURBAN, or OUTSTATION based on spatial and naming evidence."""
        # Aggregate stops per route
        route_stops: Dict[str, Set[str]] = {rid: set() for rid in routes}
        route_trip_counts: Dict[str, int] = {rid: 0 for rid in routes}

        for tid, t in trips.items():
            route_trip_counts[t.route_id] = route_trip_counts.get(t.route_id, 0) + 1
            if tid in trip_stop_ids:
                route_stops[t.route_id].update(trip_stop_ids[tid])

        decisions: Dict[str, FilteringDecision] = {}

        for rid, route in routes.items():
            sids = route_stops.get(rid, set())
            if not sids:
                # Route has no stops or no trips
                decisions[rid] = FilteringDecision(
                    route_id=rid,
                    route_short_name=route.route_short_name,
                    route_long_name=route.route_long_name,
                    classification="URBAN",
                    max_stop_distance_km=0.0,
                    has_outstation_keyword=False,
                    reason="No scheduled stops or inactive route",
                    trip_count=route_trip_counts.get(rid, 0),
                )
                continue

            max_dist = max(stop_metrics[sid][0] for sid in sids)
            has_kw = any(stop_metrics[sid][1] for sid in sids) or any(
                kw in route.route_short_name.upper() or kw in route.route_long_name.upper()
                for kw in self.config.outstation_keywords
            )

            # Decision logic with multi-evidence hierarchy
            if max_dist > self.config.outstation_cutoff_km:
                classification = "OUTSTATION"
                reason = f"Extreme distance: Max stop distance {max_dist:.1f} km exceeds {self.config.outstation_cutoff_km} km cutoff"
            elif has_kw and max_dist > 25.0:
                classification = "OUTSTATION"
                reason = f"Outstation carrier markers: Serves KSRTC/regional stands with max dist {max_dist:.1f} km"
            elif max_dist > self.config.urban_cutoff_km:
                classification = "SUBURBAN"
                reason = f"Suburban / Peri-urban commuter corridor: Max stop dist {max_dist:.1f} km within ({self.config.urban_cutoff_km}, {self.config.suburban_cutoff_km}] km"
            else:
                classification = "URBAN"
                reason = f"Within core metropolitan boundary: Max stop dist {max_dist:.1f} km <= {self.config.urban_cutoff_km} km"

            decisions[rid] = FilteringDecision(
                route_id=rid,
                route_short_name=route.route_short_name,
                route_long_name=route.route_long_name,
                classification=classification,
                max_stop_distance_km=round(max_dist, 2),
                has_outstation_keyword=has_kw,
                reason=reason,
                trip_count=route_trip_counts.get(rid, 0),
            )

        return decisions

    def apply_filter(
        self,
        routes: Dict[str, Route],
        trips: Dict[str, Trip],
        stops: Dict[str, Stop],
        decisions: Dict[str, FilteringDecision],
    ) -> Tuple[Set[str], Set[str], Set[str], UrbanFilteringSummary]:
        """Filter dataset entities based on configured filtering_mode.

        Returns:
            Tuple of (retained_route_ids, retained_trip_ids, retained_stop_ids, summary).
        """
        mode = self.config.filtering_mode.upper()
        if mode == "ALL":
            allowed_classes = {"URBAN", "SUBURBAN", "OUTSTATION"}
        elif mode == "METROPOLITAN":
            allowed_classes = {"URBAN", "SUBURBAN"}
        else:  # 'STRICT_URBAN' default
            allowed_classes = {"URBAN"}

        retained_routes = {
            rid for rid, d in decisions.items() if d.classification in allowed_classes
        }
        retained_trips = {
            tid for tid, t in trips.items() if t.route_id in retained_routes
        }

        # Track exclusion reasons
        exclusion_breakdown: Dict[str, int] = {
            "OUTSTATION_EXTREME_DISTANCE": 0,
            "OUTSTATION_OPERATOR_KEYWORD": 0,
            "SUBURBAN_EXCLUDED_BY_STRICT_MODE": 0,
        }

        borderline_routes: List[FilteringDecision] = []
        for rid, d in decisions.items():
            if d.classification == "SUBURBAN":
                borderline_routes.append(d)
                if mode == "STRICT_URBAN":
                    exclusion_breakdown["SUBURBAN_EXCLUDED_BY_STRICT_MODE"] += 1
            elif d.classification == "OUTSTATION":
                if d.max_stop_distance_km > self.config.outstation_cutoff_km:
                    exclusion_breakdown["OUTSTATION_EXTREME_DISTANCE"] += 1
                else:
                    exclusion_breakdown["OUTSTATION_OPERATOR_KEYWORD"] += 1

        summary = UrbanFilteringSummary(
            mode=mode,
            total_routes_before=len(routes),
            total_trips_before=len(trips),
            total_stops_before=len(stops),
            routes_retained=len(retained_routes),
            trips_retained=len(retained_trips),
            stops_retained=0,  # Will be populated after stop_times filtering
            routes_excluded=len(routes) - len(retained_routes),
            trips_excluded=len(trips) - len(retained_trips),
            stops_excluded=0,
            exclusion_breakdown=exclusion_breakdown,
            borderline_routes=borderline_routes,
        )

        return retained_routes, retained_trips, set(), summary
