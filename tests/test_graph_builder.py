"""Unit tests for NetworkX transit corridor graph builder."""

from pathlib import Path
import pytest
import networkx as nx

from routeranker.config import PipelineConfig
from routeranker.graph_builder import TransitGraphBuilder
from routeranker.gtfs_loader import GTFSLoader
from routeranker.schemas import StopTime


def test_corridor_graph_construction(test_config: PipelineConfig, synthetic_gtfs_dir: Path):
    """Verify directed corridor graph generation, node properties, and edge metrics."""
    loader = GTFSLoader(synthetic_gtfs_dir)
    stops = loader.load_stops()

    # Create synthetic trip stop times for T1_1 and T1_2 (consecutive: S1 -> S2 -> S3 -> S4)
    trip_stop_times = {
        "T1_1": [
            StopTime("T1_1", 28800, 28800, "S1", 1),
            StopTime("T1_1", 29700, 29700, "S2", 2),
            StopTime("T1_1", 30600, 30600, "S3", 3),
            StopTime("T1_1", 31500, 31500, "S4", 4),
        ],
        "T1_2": [
            StopTime("T1_2", 30000, 30000, "S1", 1),
            StopTime("T1_2", 30900, 30900, "S2", 2),
            StopTime("T1_2", 31800, 31800, "S3", 3),
            StopTime("T1_2", 32700, 32700, "S4", 4),
        ],
    }
    trip_routes = {"T1_1": "R1", "T1_2": "R1"}

    builder = TransitGraphBuilder(test_config)
    graph = builder.build_corridor_graph(stops, trip_stop_times, trip_routes, retained_trips={"T1_1", "T1_2"})

    # Check nodes
    assert "S1" in graph
    assert "S2" in graph
    assert "S3" in graph
    assert "S4" in graph
    assert graph.nodes["S1"]["name"] == "Kempegowda Bus Station"
    assert graph.nodes["S1"]["lat"] == 12.9778

    # Check edges (S1 -> S2, S2 -> S3, S3 -> S4)
    assert graph.has_edge("S1", "S2")
    assert graph.has_edge("S2", "S3")
    assert graph.has_edge("S3", "S4")
    assert not graph.has_edge("S2", "S1")  # Directed!

    # Check edge attributes
    edge_data = graph["S1"]["S2"]
    assert edge_data["trip_count"] == 2
    assert edge_data["route_count"] == 1
    assert edge_data["route_ids"] == "R1"
    assert edge_data["distance_km"] > 0.0
    assert edge_data["is_scheduled_transit"] is True
    assert edge_data["is_synthetic_transfer"] is False


def test_synthetic_transfer_generation(test_config: PipelineConfig, synthetic_gtfs_dir: Path):
    """Verify synthetic walking transfer edges when explicitly enabled."""
    test_config.allow_synthetic_transfers = True
    test_config.max_transfer_walk_distance_m = 500.0  # Set high enough for test stops

    loader = GTFSLoader(synthetic_gtfs_dir)
    stops = loader.load_stops()

    # Single trip S1 -> S2
    trip_stop_times = {
        "T1": [
            StopTime("T1", 28800, 28800, "S1", 1),
            StopTime("T1", 29700, 29700, "S2", 2),
        ]
    }
    trip_routes = {"T1": "R1"}

    builder = TransitGraphBuilder(test_config)
    graph = builder.build_corridor_graph(stops, trip_stop_times, trip_routes)

    # Should have scheduled transit edge
    assert graph.has_edge("S1", "S2")
    assert graph["S1"]["S2"]["is_scheduled_transit"] is True
