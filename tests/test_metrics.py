"""Unit tests for baseline metrics evaluation engine."""

from pathlib import Path
import pytest
import networkx as nx

from routeranker.config import PipelineConfig
from routeranker.metrics import MetricsCalculator
from routeranker.schemas import Route, Stop, StopTime, Trip


def test_shape_length_calculation(test_config: PipelineConfig):
    """Verify polyline shape length calculation."""
    calc = MetricsCalculator(test_config)
    # 2 points roughly 111 km apart (1 deg latitude)
    shapes = {
        "SHP_TEST": [
            (12.0, 77.0, 1),
            (13.0, 77.0, 2),
        ]
    }
    lengths = calc.calculate_shape_lengths(shapes)
    assert 110.0 <= lengths["SHP_TEST"] <= 112.0


def test_vkt_calculation(test_config: PipelineConfig):
    """Verify daily VKT calculation under both Shape and Stop Chord methods."""
    calc = MetricsCalculator(test_config)
    stops = {
        "S1": Stop("S1", "Stop 1", 12.9778, 77.5727),
        "S2": Stop("S2", "Stop 2", 12.9756, 77.6066),
    }
    trips = {
        "T1": Trip("T1", "R1", "1", shape_id="SHP1"),
        "T2": Trip("T2", "R1", "1", shape_id="SHP1"),
    }
    trip_stop_times = {
        "T1": [StopTime("T1", 0, 0, "S1", 1), StopTime("T1", 600, 600, "S2", 2)],
        "T2": [StopTime("T2", 0, 0, "S1", 1), StopTime("T2", 600, 600, "S2", 2)],
    }
    shape_lengths = {"SHP1": 4.5}  # 4.5 km per trip

    shape_vkt, chord_vkt, ratio = calc.calculate_vkt(
        trips, trip_stop_times, stops, shape_lengths, retained_trips={"T1", "T2"}
    )

    # 2 trips of 4.5 km = 9.0 km
    assert shape_vkt == 9.0
    assert chord_vkt > 0.0
    assert ratio >= 1.0


def test_headway_calculation(test_config: PipelineConfig):
    """Verify route headway calculation across departure intervals."""
    calc = MetricsCalculator(test_config)
    trips = {
        "T1": Trip("T1", "R1", "1", direction_id="0"),
        "T2": Trip("T2", "R1", "1", direction_id="0"),
        "T3": Trip("T3", "R1", "1", direction_id="0"),
    }
    # Departures at 08:00 (28800), 08:15 (29700), 08:30 (30600) -> 15 min headways
    trip_stop_times = {
        "T1": [StopTime("T1", 28800, 28800, "S1", 1), StopTime("T1", 29400, 29400, "S2", 2)],
        "T2": [StopTime("T2", 29700, 29700, "S1", 1), StopTime("T2", 30300, 30300, "S2", 2)],
        "T3": [StopTime("T3", 30600, 30600, "S1", 1), StopTime("T3", 31200, 31200, "S2", 2)],
    }

    metrics = calc.calculate_headways(trips, trip_stop_times, {"T1", "T2", "T3"})
    assert len(metrics) == 1
    m = metrics[0]
    assert m.route_id == "R1"
    assert m.trip_count == 3
    assert m.mean_headway_min == 15.0
    assert m.min_headway_min == 15.0
    assert m.max_headway_min == 15.0
    assert m.morning_peak_trips == 3


def test_headway_bidirectional_separation(test_config: PipelineConfig):
    """Verify that opposite directions on the same route are not conflated."""
    calc = MetricsCalculator(test_config)
    # R1 has 2 trips in dir 0 (dep 08:00, 08:30 -> headway 30 min)
    # and 2 trips in dir 1 (dep 08:05, 08:35 -> headway 30 min)
    # If conflated, headways would be 5 min (08:00, 08:05, 08:30, 08:35)
    trips = {
        "T1_0": Trip("T1_0", "R1", "1", direction_id="0"),
        "T2_0": Trip("T2_0", "R1", "1", direction_id="0"),
        "T1_1": Trip("T1_1", "R1", "1", direction_id="1"),
        "T2_1": Trip("T2_1", "R1", "1", direction_id="1"),
    }
    trip_stop_times = {
        "T1_0": [StopTime("T1_0", 28800, 28800, "S1", 1), StopTime("T1_0", 29400, 29400, "S2", 2)],
        "T2_0": [StopTime("T2_0", 30600, 30600, "S1", 1), StopTime("T2_0", 31200, 31200, "S2", 2)],
        "T1_1": [StopTime("T1_1", 29100, 29100, "S2", 1), StopTime("T1_1", 29700, 29700, "S1", 2)],
        "T2_1": [StopTime("T2_1", 30900, 30900, "S2", 1), StopTime("T2_1", 31500, 31500, "S1", 2)],
    }

    metrics = calc.calculate_headways(trips, trip_stop_times, {"T1_0", "T2_0", "T1_1", "T2_1"})
    assert len(metrics) == 2
    # Both directions must have 30 min headway, NOT 5 min!
    for m in metrics:
        assert m.mean_headway_min == 30.0



def test_circuity_calculation(test_config: PipelineConfig):
    """Verify route circuity for linear route and loop detection."""
    calc = MetricsCalculator(test_config)
    routes = {"R1": Route("R1", "1", "335", "Majestic - Whitefield", 3)}
    stops = {
        "S1": Stop("S1", "Majestic", 12.9778, 77.5727),
        "S2": Stop("S2", "Whitefield", 12.9698, 77.7499),
    }
    trips = {"T1": Trip("T1", "R1", "1", shape_id="SHP1")}
    trip_stop_times = {
        "T1": [StopTime("T1", 0, 0, "S1", 1), StopTime("T1", 1800, 1800, "S2", 2)]
    }
    # Actual road distance ~ 25 km, Euclidean ~ 19.3 km -> Circuity ~ 1.3
    shape_lengths = {"SHP1": 25.0}

    circ_metrics, summary = calc.calculate_route_circuity(
        routes, trips, trip_stop_times, stops, shape_lengths, {"R1"}
    )
    assert len(circ_metrics) == 1
    assert circ_metrics[0].is_circular_loop is False
    assert 1.2 <= circ_metrics[0].circuity <= 1.4
    assert summary["average"] == circ_metrics[0].circuity
