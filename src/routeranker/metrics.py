"""Baseline transit network metrics evaluation engine.

Computes mathematically rigorous, defensible baseline metrics from validated GTFS entities and graph topology.
Distinguishes directly observed timetable facts from calculated indicators and cost proxies.
"""

import logging
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import networkx as nx

from routeranker.config import PipelineConfig
from routeranker.schemas import Route, Stop, StopTime, Trip
from routeranker.urban_filter import haversine_distance_km

logger = logging.getLogger(__name__)


@dataclass
class RouteCircuityMetric:
    route_id: str
    route_short_name: str
    shape_length_km: float
    euclidean_endpoint_dist_km: float
    circuity: Optional[float]
    is_circular_loop: bool


@dataclass
class RouteHeadwayMetric:
    route_id: str
    direction_id: str
    trip_count: int
    mean_headway_min: Optional[float]
    median_headway_min: Optional[float]
    min_headway_min: Optional[float]
    max_headway_min: Optional[float]
    morning_peak_trips: int  # 07:00 - 10:00
    offpeak_trips: int       # 10:00 - 16:00
    evening_peak_trips: int  # 16:00 - 20:00
    night_trips: int         # 20:00 - 24:00


@dataclass
class BaselineMetricsReport:
    # 1. Volume & Inventory Counts
    total_routes_raw: int
    total_trips_raw: int
    total_stops_raw: int
    total_stop_times_raw: int
    total_shapes_raw: int

    # 2. Filtered Urban Network Scope
    urban_filtering_mode: str
    routes_urban: int
    trips_urban: int
    stops_urban: int
    stop_times_urban: int

    # 3. Vehicle-Kilometers Traveled (VKT)
    # Methodology A: Complete shape polyline length for every scheduled trip
    shape_based_vkt_km: float
    # Methodology B: Sum of consecutive stop-to-stop great-circle chord distances
    stop_to_stop_vkt_km: float
    shape_to_stop_chord_ratio: float

    # 4. Estimated Operational Cost Proxy (Label: Statistical Proxy, Not Actual Cost)
    benchmark_cpkm_inr: float
    estimated_daily_operating_cost_inr: float
    estimated_annual_operating_cost_inr: float
    operating_cost_disclaimer: str

    # 5. Route Geometry & Circuity
    evaluated_routes_circuity_count: int
    average_route_circuity: float
    median_route_circuity: float
    min_route_circuity: float
    max_route_circuity: float
    circular_loop_routes_count: int

    # 6. Service Frequency & Headways
    average_peak_headway_min: float
    average_offpeak_headway_min: float
    routes_with_headways_calculated: int

    # 7. Corridor Overlap & Stop Redundancy
    max_routes_on_single_stop: int
    average_routes_per_stop: float
    max_routes_on_single_corridor_edge: int
    average_routes_per_corridor_edge: float

    # 8. Graph Topology & Connectivity
    graph_nodes: int
    graph_directed_edges: int
    weakly_connected_components: int
    strongly_connected_components: int
    giant_weakly_connected_component_nodes: int
    giant_weakly_connected_component_ratio: float


class MetricsCalculator:
    """Calculates defensible baseline metrics for the RouteRanker transit optimization platform."""

    def __init__(self, config: PipelineConfig):
        self.config = config

    def calculate_shape_lengths(
        self, shapes: Dict[str, List[Tuple[float, float, int]]]
    ) -> Dict[str, float]:
        """Compute exact cumulative polyline length in kilometers for each shape_id."""
        lengths: Dict[str, float] = {}
        for shp_id, pts in shapes.items():
            if len(pts) < 2:
                lengths[shp_id] = 0.0
                continue
            total_km = 0.0
            for i in range(len(pts) - 1):
                d = haversine_distance_km(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
                total_km += d
            lengths[shp_id] = round(total_km, 3)
        return lengths

    def calculate_route_circuity(
        self,
        routes: Dict[str, Route],
        trips: Dict[str, Trip],
        trip_stop_times: Dict[str, List[StopTime]],
        stops: Dict[str, Stop],
        shape_lengths: Dict[str, float],
        retained_routes: Set[str],
    ) -> Tuple[List[RouteCircuityMetric], Dict[str, float]]:
        """Compute circuity index (shape_length / euclidean_endpoints) for each retained route.

        Circuity = Shape Length / Euclidean Distance.
        For circular loop routes (endpoints < 0.1 km apart), circuity is marked as loop rather than division by zero.
        """
        # Map route_id to a representative trip with valid shape
        route_rep_trip: Dict[str, Trip] = {}
        for tid, t in trips.items():
            if t.route_id in retained_routes and t.shape_id in shape_lengths:
                if t.route_id not in route_rep_trip:
                    route_rep_trip[t.route_id] = t

        circuity_metrics: List[RouteCircuityMetric] = []
        valid_circuities: List[float] = []

        for rid in sorted(retained_routes):
            route = routes.get(rid)
            if not route or rid not in route_rep_trip:
                continue

            t = route_rep_trip[rid]
            shp_len = shape_lengths.get(t.shape_id or "", 0.0)
            st_list = trip_stop_times.get(t.trip_id, [])
            if len(st_list) < 2 or shp_len <= 0.0:
                continue

            sorted_st = sorted(st_list, key=lambda x: x.stop_sequence)
            origin_stop = stops.get(sorted_st[0].stop_id)
            dest_stop = stops.get(sorted_st[-1].stop_id)

            if not origin_stop or not dest_stop:
                continue

            euclidean_dist = haversine_distance_km(
                origin_stop.stop_lat, origin_stop.stop_lon, dest_stop.stop_lat, dest_stop.stop_lon
            )

            # Circular / Loop route check (< 100 meters between start and end)
            if euclidean_dist < 0.1:
                circuity_metrics.append(
                    RouteCircuityMetric(
                        route_id=rid,
                        route_short_name=route.route_short_name,
                        shape_length_km=round(shp_len, 2),
                        euclidean_endpoint_dist_km=round(euclidean_dist, 2),
                        circuity=None,
                        is_circular_loop=True,
                    )
                )
            else:
                circ = shp_len / euclidean_dist
                # Filter out absurd anomalies (e.g. out-of-order coordinate artifacts)
                if 1.0 <= circ <= 10.0:
                    valid_circuities.append(circ)
                circuity_metrics.append(
                    RouteCircuityMetric(
                        route_id=rid,
                        route_short_name=route.route_short_name,
                        shape_length_km=round(shp_len, 2),
                        euclidean_endpoint_dist_km=round(euclidean_dist, 2),
                        circuity=round(circ, 2),
                        is_circular_loop=False,
                    )
                )

        circ_summary = {
            "average": round(sum(valid_circuities) / len(valid_circuities), 2) if valid_circuities else 1.0,
            "median": round(sorted(valid_circuities)[len(valid_circuities) // 2], 2) if valid_circuities else 1.0,
            "min": round(min(valid_circuities), 2) if valid_circuities else 1.0,
            "max": round(max(valid_circuities), 2) if valid_circuities else 1.0,
            "count": len(valid_circuities),
            "loops": sum(1 for m in circuity_metrics if m.is_circular_loop),
        }
        return circuity_metrics, circ_summary

    def calculate_vkt(
        self,
        trips: Dict[str, Trip],
        trip_stop_times: Dict[str, List[StopTime]],
        stops: Dict[str, Stop],
        shape_lengths: Dict[str, float],
        retained_trips: Set[str],
    ) -> Tuple[float, float, float]:
        """Compute daily scheduled Vehicle-Kilometers Traveled (VKT) using both Shape and Stop-to-Stop methods.

        Returns (shape_vkt_km, stop_to_stop_vkt_km, ratio).
        """
        shape_vkt = 0.0
        stop_to_stop_vkt = 0.0

        for tid in retained_trips:
            t = trips.get(tid)
            if not t:
                continue

            # Method A: Polyline shape length
            if t.shape_id and t.shape_id in shape_lengths:
                shape_vkt += shape_lengths[t.shape_id]

            # Method B: Consecutive stop chord distances
            st_list = trip_stop_times.get(tid, [])
            if len(st_list) >= 2:
                sorted_st = sorted(st_list, key=lambda x: x.stop_sequence)
                for i in range(len(sorted_st) - 1):
                    s_u = stops.get(sorted_st[i].stop_id)
                    s_v = stops.get(sorted_st[i + 1].stop_id)
                    if s_u and s_v:
                        d = haversine_distance_km(s_u.stop_lat, s_u.stop_lon, s_v.stop_lat, s_v.stop_lon)
                        stop_to_stop_vkt += d

        ratio = round(shape_vkt / stop_to_stop_vkt, 4) if stop_to_stop_vkt > 0 else 1.0
        return round(shape_vkt, 2), round(stop_to_stop_vkt, 2), ratio

    def calculate_headways(
        self,
        trips: Dict[str, Trip],
        trip_stop_times: Dict[str, List[StopTime]],
        retained_trips: Set[str],
    ) -> List[RouteHeadwayMetric]:
        """Calculate scheduled departure headways and time-of-day trip distributions per route and direction."""
        # Group trips by (route_id, direction_id)
        # Store origin departure time (seconds since midnight)
        route_dir_trips: Dict[Tuple[str, str], List[int]] = defaultdict(list)

        for tid in retained_trips:
            t = trips.get(tid)
            if not t:
                continue
            st_list = trip_stop_times.get(tid, [])
            if not st_list:
                continue

            sorted_st = sorted(st_list, key=lambda x: x.stop_sequence)
            origin_dep = sorted_st[0].departure_time_sec
            dir_id = t.direction_id or "0"
            route_dir_trips[(t.route_id, dir_id)].append(origin_dep)

        headway_metrics: List[RouteHeadwayMetric] = []

        for (rid, dir_id), dep_times in sorted(route_dir_trips.items()):
            dep_times.sort()
            trip_count = len(dep_times)

            # Time of day bins
            # Morning Peak: 07:00 (25200) to 10:00 (36000)
            # Off-Peak: 10:00 (36000) to 16:00 (57600)
            # Evening Peak: 16:00 (57600) to 20:00 (72000)
            # Night: 20:00 (72000) to 24:00 (86400)
            morning = sum(1 for t in dep_times if 25200 <= t < 36000)
            offpeak = sum(1 for t in dep_times if 36000 <= t < 57600)
            evening = sum(1 for t in dep_times if 57600 <= t < 72000)
            night = sum(1 for t in dep_times if 72000 <= t < 86400)

            if trip_count >= 2:
                diffs_min = [
                    (dep_times[i + 1] - dep_times[i]) / 60.0
                    for i in range(trip_count - 1)
                ]
                mean_h = round(sum(diffs_min) / len(diffs_min), 1)
                med_h = round(sorted(diffs_min)[len(diffs_min) // 2], 1)
                min_h = round(min(diffs_min), 1)
                max_h = round(max(diffs_min), 1)
            else:
                mean_h = med_h = min_h = max_h = None

            headway_metrics.append(
                RouteHeadwayMetric(
                    route_id=rid,
                    direction_id=dir_id,
                    trip_count=trip_count,
                    mean_headway_min=mean_h,
                    median_headway_min=med_h,
                    min_headway_min=min_h,
                    max_headway_min=max_h,
                    morning_peak_trips=morning,
                    offpeak_trips=offpeak,
                    evening_peak_trips=evening,
                    night_trips=night,
                )
            )

        return headway_metrics

    def calculate_corridor_overlap(
        self, graph: nx.DiGraph
    ) -> Tuple[int, float, int, float]:
        """Compute stop and corridor edge sharing / overlap metrics across routes."""
        # 1. Routes per stop
        routes_per_stop: Dict[str, Set[str]] = defaultdict(set)
        # 2. Routes per corridor edge
        routes_per_edge: List[int] = []

        for u, v, data in graph.edges(data=True):
            rc = data.get("route_count", 0)
            r_str = data.get("route_ids", "")
            r_list = r_str.split(",") if r_str else []
            routes_per_edge.append(rc)
            for r in r_list:
                if r:
                    routes_per_stop[u].add(r)
                    routes_per_stop[v].add(r)

        stop_counts = [len(r_set) for r_set in routes_per_stop.values()]
        max_routes_stop = max(stop_counts) if stop_counts else 0
        avg_routes_stop = round(sum(stop_counts) / len(stop_counts), 2) if stop_counts else 0.0

        max_routes_edge = max(routes_per_edge) if routes_per_edge else 0
        avg_routes_edge = round(sum(routes_per_edge) / len(routes_per_edge), 2) if routes_per_edge else 0.0

        return max_routes_stop, avg_routes_stop, max_routes_edge, avg_routes_edge

    def calculate_connectivity(self, graph: nx.DiGraph) -> Tuple[int, int, int, float]:
        """Compute weakly and strongly connected components on the directed transit corridor graph."""
        num_wcc = nx.number_weakly_connected_components(graph)
        num_scc = nx.number_strongly_connected_components(graph)

        wcc_sizes = [len(c) for c in nx.weakly_connected_components(graph)]
        giant_size = max(wcc_sizes) if wcc_sizes else 0
        giant_ratio = round(giant_size / graph.number_of_nodes(), 4) if graph.number_of_nodes() > 0 else 0.0

        return num_wcc, num_scc, giant_size, giant_ratio
