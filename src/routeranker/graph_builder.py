"""Transit graph builder module using NetworkX.

Constructs directed corridor graphs with node coordinates and route/trip provenance.
Distinguishes observed scheduled transit edges from synthetic walk assumptions.
"""

import json
import logging
import math
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import networkx as nx

from routeranker.config import PipelineConfig
from routeranker.schemas import Stop, StopTime
from routeranker.urban_filter import haversine_distance_km

logger = logging.getLogger(__name__)


class TransitGraphBuilder:
    """Builds and analyzes directed transit network graphs from validated GTFS entities."""

    def __init__(self, config: PipelineConfig):
        self.config = config

    def build_corridor_graph(
        self,
        stops: Dict[str, Stop],
        trip_stop_times: Dict[str, List[StopTime]],
        trip_routes: Dict[str, str],
        retained_trips: Optional[Set[str]] = None,
    ) -> nx.DiGraph:
        """Construct a directed transit corridor graph G = (V, E).

        Nodes V represent validated physical stops.
        Directed edges E represent observed scheduled transit segments between consecutive stops.
        Edge attributes capture distance, aggregate trip counts, unique route IDs, and scheduled travel times.
        """
        graph = nx.DiGraph()
        graph.graph["name"] = "RouteRanker_Bengaluru_Transit_Corridor_Graph"
        graph.graph["crs"] = "EPSG:4326"
        graph.graph["description"] = (
            "Directed transit network graph derived from BMTC GTFS scheduled stop sequences. "
            "Edges represent observed scheduled transit operations."
        )

        # 1. Add all stops as nodes
        for sid, s in stops.items():
            graph.add_node(
                sid,
                stop_id=sid,
                name=s.stop_name,
                lat=s.stop_lat,
                lon=s.stop_lon,
                zone_id=s.zone_id or "",
                location_type=s.location_type or "0",
                parent_station=s.parent_station or "",
            )

        # 2. Accumulate consecutive stop segments across trips
        # Key: (from_stop, to_stop) -> dict of metrics
        segments: Dict[Tuple[str, str], Dict] = {}

        for tid, st_list in trip_stop_times.items():
            if retained_trips is not None and tid not in retained_trips:
                continue

            rid = trip_routes.get(tid, "")
            # Ensure st_list is sorted by sequence
            sorted_st = sorted(st_list, key=lambda x: x.stop_sequence)

            for i in range(len(sorted_st) - 1):
                u = sorted_st[i].stop_id
                v = sorted_st[i + 1].stop_id

                if u not in stops or v not in stops:
                    continue

                runtime_sec = sorted_st[i + 1].arrival_time_sec - sorted_st[i].departure_time_sec
                # If negative (e.g. data anomaly), treat as 0
                runtime_sec = max(0, runtime_sec)

                edge_key = (u, v)
                if edge_key not in segments:
                    stop_u = stops[u]
                    stop_v = stops[v]
                    dist_km = haversine_distance_km(
                        stop_u.stop_lat, stop_u.stop_lon, stop_v.stop_lat, stop_v.stop_lon
                    )
                    segments[edge_key] = {
                        "distance_km": round(dist_km, 4),
                        "trip_count": 0,
                        "routes": set(),
                        "runtimes_sec": [],
                    }

                seg = segments[edge_key]
                seg["trip_count"] += 1
                if rid:
                    seg["routes"].add(rid)
                seg["runtimes_sec"].append(runtime_sec)

        # 3. Add aggregated edges to graph
        for (u, v), data in segments.items():
            runtimes = data["runtimes_sec"]
            min_runtime = min(runtimes) if runtimes else 0
            mean_runtime = sum(runtimes) / len(runtimes) if runtimes else 0.0

            graph.add_edge(
                u,
                v,
                distance_km=data["distance_km"],
                trip_count=data["trip_count"],
                route_count=len(data["routes"]),
                route_ids=",".join(sorted(data["routes"])),
                min_runtime_sec=min_runtime,
                mean_runtime_sec=round(mean_runtime, 1),
                is_scheduled_transit=True,
                is_synthetic_transfer=False,
            )

        # Remove isolated nodes if they have 0 degree in the filtered subgraph
        isolated = [node for node, degree in dict(graph.degree()).items() if degree == 0]
        graph.remove_nodes_from(isolated)

        # 4. Optional synthetic transfer edges (only if explicitly enabled)
        if self.config.allow_synthetic_transfers:
            self._add_synthetic_transfers(graph, stops)

        logger.info(
            f"Built transit corridor graph: {graph.number_of_nodes():,} nodes, "
            f"{graph.number_of_edges():,} directed edges."
        )
        return graph

    def _add_synthetic_transfers(self, graph: nx.DiGraph, stops: Dict[str, Stop]) -> None:
        """Add bidirectional pedestrian transfer edges between close stops.

        WARNING: This introduces synthetic assumptions and must not be confused with scheduled transit.
        """
        max_dist_m = self.config.max_transfer_walk_distance_m
        walk_speed = self.config.assumed_walking_speed_mps
        nodes = list(graph.nodes())

        added_transfers = 0
        for i in range(len(nodes)):
            u = nodes[i]
            s_u = stops[u]
            for j in range(i + 1, len(nodes)):
                v = nodes[j]
                s_v = stops[v]
                dist_km = haversine_distance_km(s_u.stop_lat, s_u.stop_lon, s_v.stop_lat, s_v.stop_lon)
                dist_m = dist_km * 1000.0
                if 0 < dist_m <= max_dist_m:
                    walk_time_sec = round(dist_m / walk_speed, 1)
                    # Add bidirectional transfer
                    graph.add_edge(
                        u,
                        v,
                        distance_km=round(dist_km, 4),
                        trip_count=0,
                        route_count=0,
                        route_ids="",
                        min_runtime_sec=walk_time_sec,
                        mean_runtime_sec=walk_time_sec,
                        is_scheduled_transit=False,
                        is_synthetic_transfer=True,
                    )
                    graph.add_edge(
                        v,
                        u,
                        distance_km=round(dist_km, 4),
                        trip_count=0,
                        route_count=0,
                        route_ids="",
                        min_runtime_sec=walk_time_sec,
                        mean_runtime_sec=walk_time_sec,
                        is_scheduled_transit=False,
                        is_synthetic_transfer=True,
                    )
                    added_transfers += 2

        logger.info(f"Added {added_transfers} synthetic pedestrian transfer edges (walk <= {max_dist_m}m).")

    def export_graphml(self, graph: nx.DiGraph, output_path: Path) -> None:
        """Serialize NetworkX transit graph to GraphML format."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        nx.write_graphml(graph, str(output_path))
        logger.info(f"Exported transit corridor graph to GraphML: {output_path}")

    def export_node_link_json(self, graph: nx.DiGraph, output_path: Path) -> None:
        """Export transit graph as node-link JSON for web or machine-learning consumption."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        data = nx.node_link_data(graph)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Exported transit corridor graph to JSON: {output_path}")
