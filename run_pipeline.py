"""CLI entry point to execute the RouteRanker Milestone 2 pipeline."""

import argparse
import sys
from pathlib import Path

# Add src to sys.path so routeranker package is importable without pip install -e .
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from routeranker.config import PipelineConfig
from routeranker.pipeline import RouteRankerPipeline


def main():
    parser = argparse.ArgumentParser(
        description="RouteRanker Milestone 2: Reproducible GTFS Ingestion, Outstation Filtering & Baseline Transit Graph Modeling"
    )
    parser.add_argument(
        "--gtfs-dir",
        type=str,
        default="dataset/bmtc",
        help="Path to GTFS directory relative to repository root (default: dataset/bmtc)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="outputs",
        help="Directory to save generated reports and graph artifacts (default: outputs)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["STRICT_URBAN", "METROPOLITAN", "ALL"],
        default="STRICT_URBAN",
        help="Urban filtering mode: STRICT_URBAN (<=50km), METROPOLITAN (<=65km), or ALL (default: STRICT_URBAN)",
    )
    parser.add_argument(
        "--cpkm",
        type=float,
        default=83.05,
        help="Benchmark Cost Per Kilometer (CPKM) in INR for operational cost proxy (default: 83.05)",
    )

    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent
    config = PipelineConfig(
        repo_root=repo_root,
        gtfs_dir=repo_root / args.gtfs_dir,
        output_dir=repo_root / args.output_dir,
        filtering_mode=args.mode,
        benchmark_cpkm_inr=args.cpkm,
    )

    pipeline = RouteRankerPipeline(config)
    quality, filtering, metrics, graph = pipeline.run()

    print("\n" + "=" * 60)
    print("ROUTERANKER MILESTONE 2: EXECUTION SUMMARY")
    print("=" * 60)
    print(f"Data Quality:       {'PASSED (0 Errors)' if quality.error_count() == 0 else f'ISSUES DETECTED ({quality.error_count()} Errors)'}")
    print(f"Declared Routes:    {quality.total_routes:,} (Inactive: {quality.routes_without_trips})")
    print(f"Total Trips:        {quality.total_trips:,}")
    print(f"Filtering Mode:     {filtering.mode}")
    print(f"Retained Routes:    {filtering.routes_retained:,} / {filtering.total_routes_before:,}")
    print(f"Retained Trips:     {filtering.trips_retained:,} / {filtering.total_trips_before:,}")
    print(f"Active Urban Stops: {metrics.graph_nodes:,}")
    print(f"Corridor Edges:     {metrics.graph_directed_edges:,}")
    print(f"Daily Scheduled VKT: {metrics.shape_based_vkt_km:,.1f} km")
    print(f"Est. Daily Op Cost: Rs {metrics.estimated_daily_operating_cost_inr:,.2f} (@ Rs {metrics.benchmark_cpkm_inr}/km proxy)")
    print(f"Average Circuity:   {metrics.average_route_circuity:.2f}")
    print(f"Avg Morning Peak Headway: {metrics.average_peak_headway_min:.1f} min")
    print(f"Reports Generated:  {config.output_dir.resolve()}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
