"""RouteRanker Pipeline Orchestrator for Milestone 2.

Executes end-to-end reproducible ingestion, urban filtering, graph construction,
baseline metric calculation, and structured report exports.
"""

import json
import logging
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import networkx as nx

from routeranker.config import PipelineConfig
from routeranker.gtfs_loader import GTFSLoader, parse_gtfs_time
from routeranker.schemas import (
    DataQualityReport,
    FilteringDecision,
    UrbanFilteringSummary,
)
from routeranker.urban_filter import UrbanFilter, haversine_distance_km
from routeranker.metrics import BaselineMetricsReport, MetricsCalculator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("routeranker.pipeline")


class RouteRankerPipeline:
    """End-to-end pipeline manager for RouteRanker transit data processing."""

    def __init__(self, config: Optional[PipelineConfig] = None):
        self.config = config or PipelineConfig()
        self.output_dir = self.config.output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> Tuple[DataQualityReport, UrbanFilteringSummary, BaselineMetricsReport, nx.DiGraph]:
        """Execute the complete Milestone 2 pipeline."""
        logger.info(f"=== Starting RouteRanker Milestone 2 Pipeline ===")
        logger.info(f"GTFS Directory: {self.config.gtfs_dir}")
        logger.info(f"Filtering Mode: {self.config.filtering_mode}")

        # -------------------------------------------------------------
        # 1. Ingestion & GTFS Integrity Validation
        # -------------------------------------------------------------
        loader = GTFSLoader(self.config.gtfs_dir)
        loader.validate_file_existence()

        agencies = loader.load_agency()
        calendar = loader.load_calendar()
        routes = loader.load_routes(agency_ids=set(agencies.keys()))
        stops = loader.load_stops()
        trips = loader.load_trips(route_ids=set(routes.keys()), service_ids=set(calendar.keys()))
        shapes = loader.load_shapes_summary()

        logger.info(
            f"Loaded {len(agencies)} agencies, {len(routes)} routes, "
            f"{len(trips)} trips, {len(stops)} stops, {len(shapes)} shapes."
        )

        # -------------------------------------------------------------
        # 2. First Pass: Map Trip Stops for Spatial Classification
        # -------------------------------------------------------------
        # We stream stop_times to map which stops are served by which trips
        logger.info("Scanning stop_times to determine stop-route mappings...")
        trip_stop_ids: Dict[str, Set[str]] = {}
        # Also record trip origins/destinations for circuity, and origin departures for headways
        trip_terminals: Dict[str, Tuple[str, str]] = {}
        trip_origin_deps: Dict[str, int] = {}
        trip_stop_sequences: Dict[str, List[Tuple[int, str, int, int]]] = {}

        # Streaming pass through stop_times
        total_st_records = 0
        current_trip = None
        current_stops = []
        observed_st_trips = set()

        import csv

        st_file = self.config.gtfs_dir / "stop_times.txt"
        with open(st_file, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                total_st_records += 1
                tid = row["trip_id"]
                sid = row["stop_id"]
                seq = int(row["stop_sequence"])
                arr = parse_gtfs_time(row["arrival_time"]) or 0
                dep = parse_gtfs_time(row["departure_time"]) or 0

                observed_st_trips.add(tid)
                if tid not in trip_stop_ids:
                    trip_stop_ids[tid] = set()
                trip_stop_ids[tid].add(sid)

                if tid == current_trip:
                    current_stops.append((seq, sid, arr, dep))
                else:
                    if current_trip is not None and current_stops:
                        current_stops.sort(key=lambda x: x[0])
                        trip_terminals[current_trip] = (current_stops[0][1], current_stops[-1][1])
                        trip_origin_deps[current_trip] = current_stops[0][3]
                    current_trip = tid
                    current_stops = [(seq, sid, arr, dep)]

            if current_trip is not None and current_stops:
                current_stops.sort(key=lambda x: x[0])
                trip_terminals[current_trip] = (current_stops[0][1], current_stops[-1][1])
                trip_origin_deps[current_trip] = current_stops[0][3]

        loader.report.total_stop_times = total_st_records
        loader.report.trips_without_stop_times = len(set(trips.keys()) - observed_st_trips)

        # -------------------------------------------------------------
        # 3. Urban vs Outstation Filtering
        # -------------------------------------------------------------
        urban_filter = UrbanFilter(self.config)
        stop_metrics = urban_filter.evaluate_stops(stops)
        route_decisions = urban_filter.classify_routes(routes, trips, trip_stop_ids, stop_metrics)
        retained_routes, retained_trips, _, filtering_summary = urban_filter.apply_filter(
            routes, trips, stops, route_decisions
        )

        logger.info(
            f"Filtering Result [{filtering_summary.mode}]: "
            f"Retained {filtering_summary.routes_retained}/{filtering_summary.total_routes_before} routes, "
            f"{filtering_summary.trips_retained}/{filtering_summary.total_trips_before} trips."
        )

        # -------------------------------------------------------------
        # 4. Build Transit Corridor Graph (Streaming Pass 2)
        # -------------------------------------------------------------
        logger.info("Building directed transit corridor graph...")
        graph = nx.DiGraph()
        graph.graph["name"] = "RouteRanker_Bengaluru_Transit_Corridor_Graph"
        graph.graph["crs"] = "EPSG:4326"
        graph.graph["filtering_mode"] = self.config.filtering_mode

        # Add active stops as nodes
        active_stops: Set[str] = set()
        segments: Dict[Tuple[str, str], Dict] = {}
        stop_to_stop_vkt = 0.0

        current_trip = None
        current_trip_retained = False
        current_stops = []

        with open(st_file, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                tid = row["trip_id"]
                sid = row["stop_id"]
                seq = int(row["stop_sequence"])
                arr = parse_gtfs_time(row["arrival_time"]) or 0
                dep = parse_gtfs_time(row["departure_time"]) or 0

                if tid != current_trip:
                    # Process previous trip if retained
                    if current_trip is not None and current_trip_retained and len(current_stops) >= 2:
                        current_stops.sort(key=lambda x: x[0])
                        rid = trips[current_trip].route_id
                        for i in range(len(current_stops) - 1):
                            u = current_stops[i][1]
                            v = current_stops[i + 1][1]
                            active_stops.add(u)
                            active_stops.add(v)
                            s_u = stops.get(u)
                            s_v = stops.get(v)
                            if s_u and s_v:
                                edge_key = (u, v)
                                runtime = max(0, current_stops[i + 1][2] - current_stops[i][3])
                                if edge_key not in segments:
                                    d_km = haversine_distance_km(s_u.stop_lat, s_u.stop_lon, s_v.stop_lat, s_v.stop_lon)
                                    segments[edge_key] = {
                                        "distance_km": round(d_km, 4),
                                        "trip_count": 0,
                                        "routes": set(),
                                        "runtimes": [],
                                    }
                                seg = segments[edge_key]
                                seg["trip_count"] += 1
                                seg["routes"].add(rid)
                                seg["runtimes"].append(runtime)
                                stop_to_stop_vkt += segments[edge_key]["distance_km"]

                    current_trip = tid
                    current_trip_retained = tid in retained_trips
                    current_stops = [(seq, sid, arr, dep)]
                else:
                    current_stops.append((seq, sid, arr, dep))

            # Process final trip
            if current_trip is not None and current_trip_retained and len(current_stops) >= 2:
                current_stops.sort(key=lambda x: x[0])
                rid = trips[current_trip].route_id
                for i in range(len(current_stops) - 1):
                    u = current_stops[i][1]
                    v = current_stops[i + 1][1]
                    active_stops.add(u)
                    active_stops.add(v)
                    s_u = stops.get(u)
                    s_v = stops.get(v)
                    if s_u and s_v:
                        edge_key = (u, v)
                        runtime = max(0, current_stops[i + 1][2] - current_stops[i][3])
                        if edge_key not in segments:
                            d_km = haversine_distance_km(s_u.stop_lat, s_u.stop_lon, s_v.stop_lat, s_v.stop_lon)
                            segments[edge_key] = {
                                "distance_km": round(d_km, 4),
                                "trip_count": 0,
                                "routes": set(),
                                "runtimes": [],
                            }
                        seg = segments[edge_key]
                        seg["trip_count"] += 1
                        seg["routes"].add(rid)
                        seg["runtimes"].append(runtime)
                        stop_to_stop_vkt += segments[edge_key]["distance_km"]

        # Populate nodes in graph
        for sid in active_stops:
            s = stops[sid]
            graph.add_node(
                sid,
                name=s.stop_name,
                lat=s.stop_lat,
                lon=s.stop_lon,
                zone_id=s.zone_id or "",
                location_type=s.location_type or "0",
            )

        # Populate edges in graph
        for (u, v), data in segments.items():
            runtimes = data["runtimes"]
            graph.add_edge(
                u,
                v,
                distance_km=data["distance_km"],
                trip_count=data["trip_count"],
                route_count=len(data["routes"]),
                route_ids=",".join(sorted(data["routes"])),
                min_runtime_sec=min(runtimes) if runtimes else 0,
                mean_runtime_sec=round(sum(runtimes) / len(runtimes), 1) if runtimes else 0.0,
                is_scheduled_transit=True,
                is_synthetic_transfer=False,
            )

        filtering_summary.stops_retained = len(active_stops)
        filtering_summary.stops_excluded = len(stops) - len(active_stops)

        # -------------------------------------------------------------
        # 5. Calculate Defensible Baseline Metrics
        # -------------------------------------------------------------
        logger.info("Computing baseline transit network metrics...")
        calc = MetricsCalculator(self.config)
        shape_lengths = calc.calculate_shape_lengths(shapes)

        # Shape VKT
        shape_vkt = sum(
            shape_lengths.get(trips[tid].shape_id or "", 0.0)
            for tid in retained_trips
            if trips[tid].shape_id in shape_lengths
        )
        shape_vkt = round(shape_vkt, 2)
        stop_to_stop_vkt = round(stop_to_stop_vkt, 2)
        vkt_ratio = round(shape_vkt / stop_to_stop_vkt, 4) if stop_to_stop_vkt > 0 else 1.0

        # Cost proxy
        cpkm = self.config.benchmark_cpkm_inr
        daily_cost = round(shape_vkt * cpkm, 2)
        annual_cost = round(daily_cost * 365, 2)

        # Circuity
        circ_metrics, circ_summary = self._compute_circuity(
            routes, trips, trip_terminals, stops, shape_lengths, retained_routes
        )

        # Headways & Service Frequency
        headway_summary = self._compute_headway_summary(trips, trip_origin_deps, retained_trips)

        # Overlap & Connectivity
        max_r_stop, avg_r_stop, max_r_edge, avg_r_edge = calc.calculate_corridor_overlap(graph)
        num_wcc, num_scc, giant_wcc, giant_ratio = calc.calculate_connectivity(graph)

        metrics_report = BaselineMetricsReport(
            total_routes_raw=len(routes),
            total_trips_raw=len(trips),
            total_stops_raw=len(stops),
            total_stop_times_raw=total_st_records,
            total_shapes_raw=len(shapes),
            urban_filtering_mode=self.config.filtering_mode,
            routes_urban=len(retained_routes),
            trips_urban=len(retained_trips),
            stops_urban=len(active_stops),
            stop_times_urban=sum(data["trip_count"] for data in segments.values()),
            shape_based_vkt_km=shape_vkt,
            stop_to_stop_vkt_km=stop_to_stop_vkt,
            shape_to_stop_chord_ratio=vkt_ratio,
            benchmark_cpkm_inr=cpkm,
            estimated_daily_operating_cost_inr=daily_cost,
            estimated_annual_operating_cost_inr=annual_cost,
            operating_cost_disclaimer=(
                "Cost is a statistical operational proxy derived from BMTC FY 2024-25 average CPKM (Rs 83.05/km). "
                "It does not represent exact per-route accounting or fuel expenditure."
            ),
            evaluated_routes_circuity_count=circ_summary["count"],
            average_route_circuity=circ_summary["average"],
            median_route_circuity=circ_summary["median"],
            min_route_circuity=circ_summary["min"],
            max_route_circuity=circ_summary["max"],
            circular_loop_routes_count=circ_summary["loops"],
            average_peak_headway_min=headway_summary["avg_peak_headway_min"],
            average_offpeak_headway_min=headway_summary["avg_offpeak_headway_min"],
            routes_with_headways_calculated=headway_summary["routes_evaluated"],
            max_routes_on_single_stop=max_r_stop,
            average_routes_per_stop=avg_r_stop,
            max_routes_on_single_corridor_edge=max_r_edge,
            average_routes_per_corridor_edge=avg_r_edge,
            graph_nodes=graph.number_of_nodes(),
            graph_directed_edges=graph.number_of_edges(),
            weakly_connected_components=num_wcc,
            strongly_connected_components=num_scc,
            giant_weakly_connected_component_nodes=giant_wcc,
            giant_weakly_connected_component_ratio=giant_ratio,
        )

        # -------------------------------------------------------------
        # 6. Export Reports and Artifacts
        # -------------------------------------------------------------
        self._export_artifacts(loader.report, filtering_summary, metrics_report, graph)

        logger.info("=== RouteRanker Milestone 2 Pipeline Completed Successfully ===")
        return loader.report, filtering_summary, metrics_report, graph

    def _compute_circuity(
        self,
        routes: Dict,
        trips: Dict,
        trip_terminals: Dict[str, Tuple[str, str]],
        stops: Dict,
        shape_lengths: Dict[str, float],
        retained_routes: Set[str],
    ) -> Tuple[List, Dict]:
        """Compute circuity metrics using trip terminals."""
        route_rep_trip: Dict[str, str] = {}
        for tid in trips:
            t = trips[tid]
            if t.route_id in retained_routes and t.shape_id in shape_lengths:
                if t.route_id not in route_rep_trip:
                    route_rep_trip[t.route_id] = tid

        valid_circuities = []
        circ_metrics = []
        loops = 0

        for rid in sorted(retained_routes):
            tid = route_rep_trip.get(rid)
            if not tid or tid not in trip_terminals:
                continue
            shp_id = trips[tid].shape_id or ""
            shp_len = shape_lengths.get(shp_id, 0.0)
            u, v = trip_terminals[tid]
            s_u, s_v = stops.get(u), stops.get(v)
            if not s_u or not s_v or shp_len <= 0:
                continue

            euclidean = haversine_distance_km(s_u.stop_lat, s_u.stop_lon, s_v.stop_lat, s_v.stop_lon)
            if euclidean < 0.1:
                loops += 1
            else:
                circ = shp_len / euclidean
                if 1.0 <= circ <= 8.0:
                    valid_circuities.append(circ)

        summary = {
            "average": round(sum(valid_circuities) / len(valid_circuities), 2) if valid_circuities else 1.0,
            "median": round(sorted(valid_circuities)[len(valid_circuities) // 2], 2) if valid_circuities else 1.0,
            "min": round(min(valid_circuities), 2) if valid_circuities else 1.0,
            "max": round(max(valid_circuities), 2) if valid_circuities else 1.0,
            "count": len(valid_circuities),
            "loops": loops,
        }
        return circ_metrics, summary

    def _compute_headway_summary(
        self, trips: Dict, trip_origin_deps: Dict[str, int], retained_trips: Set[str]
    ) -> Dict:
        """Compute average peak and offpeak headways across retained routes, grouped by route and direction."""
        route_dir_deps = defaultdict(list)
        for tid in retained_trips:
            dep = trip_origin_deps.get(tid)
            if dep is not None:
                t = trips[tid]
                dir_id = t.direction_id or "0"
                route_dir_deps[(t.route_id, dir_id)].append(dep)

        peak_headways = []
        offpeak_headways = []

        for (rid, dir_id), deps in route_dir_deps.items():
            deps.sort()
            if len(deps) >= 2:
                # Morning peak: 7:00 (25200) to 10:00 (36000)
                m_deps = [d for d in deps if 25200 <= d <= 36000]
                if len(m_deps) >= 2:
                    diffs = [(m_deps[i + 1] - m_deps[i]) / 60.0 for i in range(len(m_deps) - 1)]
                    peak_headways.append(sum(diffs) / len(diffs))

                # Offpeak: 10:00 (36000) to 16:00 (57600)
                o_deps = [d for d in deps if 36000 <= d <= 57600]
                if len(o_deps) >= 2:
                    diffs = [(o_deps[i + 1] - o_deps[i]) / 60.0 for i in range(len(o_deps) - 1)]
                    offpeak_headways.append(sum(diffs) / len(diffs))

        return {
            "routes_evaluated": len(route_dir_deps),
            "avg_peak_headway_min": round(sum(peak_headways) / len(peak_headways), 1) if peak_headways else 0.0,
            "avg_offpeak_headway_min": round(sum(offpeak_headways) / len(offpeak_headways), 1) if offpeak_headways else 0.0,
        }

    def _export_artifacts(
        self,
        quality: DataQualityReport,
        filtering: UrbanFilteringSummary,
        metrics: BaselineMetricsReport,
        graph: nx.DiGraph,
    ) -> None:
        """Export machine-readable JSON artifacts, GraphML graph, and Markdown summary."""
        # 1. Data Quality JSON
        quality_path = self.output_dir / "data_quality_report.json"
        quality_dict = asdict(quality)
        with open(quality_path, "w", encoding="utf-8") as f:
            json.dump(quality_dict, f, indent=2)

        # 2. Filtering Summary JSON
        filter_path = self.output_dir / "urban_filtering_summary.json"
        filter_dict = asdict(filtering)
        with open(filter_path, "w", encoding="utf-8") as f:
            json.dump(filter_dict, f, indent=2)

        # 3. Baseline Metrics JSON
        metrics_path = self.output_dir / "baseline_metrics.json"
        metrics_dict = asdict(metrics)
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(metrics_dict, f, indent=2)

        # 4. GraphML Export
        graphml_path = self.output_dir / "transit_corridor_graph.graphml"
        nx.write_graphml(graph, str(graphml_path))

        # 5. Markdown Report
        md_path = self.output_dir / "baseline_audit_report.md"
        self._write_markdown_report(md_path, quality, filtering, metrics)

        logger.info(f"Generated all output artifacts in {self.output_dir.resolve()}")

    def _write_markdown_report(
        self,
        path: Path,
        quality: DataQualityReport,
        filtering: UrbanFilteringSummary,
        metrics: BaselineMetricsReport,
    ) -> None:
        """Write human-readable Markdown summary report."""
        content = f"""# RouteRanker: Baseline Transit Network Audit & Metrics Report

**Milestone:** Milestone 2 — GTFS Ingestion, Outstation Filtering & Baseline Modeling  
**Target City:** Bengaluru Metropolitan Area  
**Transit Agency:** Bengaluru Metropolitan Transport Corporation (BMTC)  
**Filtering Mode:** `{filtering.mode}`  

---

## 1. Data Ingestion & Quality Findings

* **Agencies:** {quality.total_agencies}
* **Declared Routes:** {quality.total_routes:,} (Routes with 0 trips: {quality.routes_without_trips})
* **Total Trips:** {quality.total_trips:,} (Trips without stop_times: {quality.trips_without_stop_times})
* **Total Stops:** {quality.total_stops:,}
* **Scheduled Stop Times:** {quality.total_stop_times:,}
* **Distinct Shape Polylines:** {quality.total_shapes:,} ({quality.total_shape_points:,} vertices)
* **Total Validation Errors:** {quality.error_count()}
* **Total Validation Warnings:** {quality.warning_count()}

---

## 2. Urban vs. Outstation Service Partitioning

To ensure route optimization operates strictly on Bengaluru's urban and suburban transit network, long-distance intercity services were identified and filtered:

* **Routes Before Filtering:** {filtering.total_routes_before:,}
* **Routes Retained (Urban Scope):** {filtering.routes_retained:,} ({filtering.routes_excluded:,} excluded)
* **Trips Retained:** {filtering.trips_retained:,} ({filtering.trips_excluded:,} excluded)
* **Stops in Urban Graph:** {filtering.stops_retained:,} ({filtering.stops_excluded:,} excluded)

### Exclusion Breakdown by Rule:
* **Extreme Distance (> 75 km from city center):** {filtering.exclusion_breakdown.get('OUTSTATION_EXTREME_DISTANCE', 0)} routes
* **Outstation Operator Keyword Markers (e.g. KSRTC stands):** {filtering.exclusion_breakdown.get('OUTSTATION_OPERATOR_KEYWORD', 0)} routes
* **Suburban / Peri-Urban Excluded (under Strict Urban mode):** {filtering.exclusion_breakdown.get('SUBURBAN_EXCLUDED_BY_STRICT_MODE', 0)} routes

---

## 3. Defensible Baseline Metrics

| Metric Category | Metric Name | Value | Methodology / Epistemic Basis |
| :--- | :--- | :---: | :--- |
| **Operational Volume** | Daily Scheduled VKT (Shape-based) | {metrics.shape_based_vkt_km:,.1f} km | **[B] Calculated:** Sum of polyline lengths for all scheduled trips |
| **Operational Volume** | Daily Scheduled VKT (Stop chord) | {metrics.stop_to_stop_vkt_km:,.1f} km | **[B] Calculated:** Direct stop-to-stop great-circle chord sum |
| **Operational Volume** | Shape-to-Chord VKT Ratio | {metrics.shape_to_stop_chord_ratio:.3f} | **[B] Calculated:** Measures roadway sinuosity relative to stop chords |
| **Cost Proxy** | Benchmark CPKM | Rs {metrics.benchmark_cpkm_inr:.2f} / km | **[A] Observed:** BMTC FY 2024-25 aggregate operational reporting |
| **Cost Proxy** | Estimated Daily Operating Cost | Rs {metrics.estimated_daily_operating_cost_inr:,.2f} | **[C] Estimated:** Daily VKT × Benchmark CPKM (Statistical proxy only) |
| **Cost Proxy** | Estimated Annual Operating Cost | Rs {metrics.estimated_annual_operating_cost_inr:,.2f} | **[C] Estimated:** Daily cost × 365 days |
| **Route Geometry** | Average Route Circuity | {metrics.average_route_circuity:.2f} | **[B] Calculated:** Shape length / Euclidean terminal distance |
| **Route Geometry** | Median Route Circuity | {metrics.median_route_circuity:.2f} | **[B] Calculated:** Median ratio across non-loop routes |
| **Route Geometry** | Circular / Loop Routes Count | {metrics.circular_loop_routes_count} | **[A] Observed:** Routes with origin-destination distance < 100m |
| **Frequency** | Average Morning Peak Headway | {metrics.average_peak_headway_min:.1f} min | **[B] Calculated:** Mean departure interval (07:00–10:00) |
| **Frequency** | Average Off-Peak Headway | {metrics.average_offpeak_headway_min:.1f} min | **[B] Calculated:** Mean departure interval (10:00–16:00) |
| **Network Density** | Max Routes on Single Stop | {metrics.max_routes_on_single_stop} | **[B] Calculated:** Majestic/KBS central terminal concentration |
| **Network Density** | Average Routes per Stop | {metrics.average_routes_per_stop:.2f} | **[B] Calculated:** Network-wide average stop sharing |
| **Network Density** | Max Routes on Single Corridor | {metrics.max_routes_on_single_corridor_edge} | **[B] Calculated:** High-concurrency trunk corridors |
| **Topology** | Graph Nodes (Transit Stops) | {metrics.graph_nodes:,} | **[A] Observed:** Active urban transit stops |
| **Topology** | Graph Directed Edges | {metrics.graph_directed_edges:,} | **[B] Calculated:** Distinct scheduled stop-to-stop corridors |
| **Topology** | Weakly Connected Components | {metrics.weakly_connected_components} | **[B] Calculated:** Connected subgraphs |
| **Topology** | Giant Component Ratio | {metrics.giant_weakly_connected_component_ratio * 100:.1f}% | **[B] Calculated:** Fraction of stops in main connected network |

---

## 4. Key Limitations & Assumptions

1. **No Passenger Demand:** Scheduled timetables do not establish passenger boardings, overcrowding, or origin-destination travel demand.
2. **Scheduled vs Actual Times:** Dwell times and trip runtimes reflect published schedules, not real-time GPS traffic congestion.
3. **Operational Cost Proxy:** The cost figure is an aggregate statistical estimate (CPKM = Rs 83.05/km). Exact per-route fleet accounting requires depot-level telemetry not present in public GTFS.
4. **Urban Boundary:** The urban cutoff radius (50 km from Majestic) is a project assumption aligning with BMRDA boundaries that can be reconfigured.
"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
