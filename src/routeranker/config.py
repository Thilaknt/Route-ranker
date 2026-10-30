"""Configuration module for RouteRanker transit data processing and baseline modeling.

All paths are relative to the repository root to ensure portability across environments.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple


@dataclass
class PipelineConfig:
    """Configuration parameters for the RouteRanker pipeline."""

    # Base paths relative to repository root
    repo_root: Path = field(default_factory=lambda: Path("."))
    gtfs_dir: Path = field(default_factory=lambda: Path("dataset/bmtc"))
    output_dir: Path = field(default_factory=lambda: Path("outputs"))

    # Auxiliary datasets
    osm_pbf_path: Path = field(default_factory=lambda: Path("dataset/india-261024.osm.pbf"))
    bmtc_kpis_path: Path = field(default_factory=lambda: Path("dataset/fda5d502-4e92-4616-a9a1-bd5d2abfb3f7.csv"))
    bmtc_divisions_path: Path = field(default_factory=lambda: Path("dataset/56f6deb2-c7ad-4ade-88fb-92190eafe19b.csv"))

    # Reference center for Bengaluru transit network (Kempegowda Bus Station / Majestic)
    bengaluru_center_lat: float = 12.9778
    bengaluru_center_lon: float = 77.5727

    # Spatial filtering thresholds (in kilometers from center)
    # 50 km corresponds approximately to the BMRDA boundary
    # 65 km accommodates peri-urban satellite terminals (Dabaspete, Nandi Hills)
    urban_cutoff_km: float = 50.0
    suburban_cutoff_km: float = 65.0
    outstation_cutoff_km: float = 75.0

    # Keywords indicative of inter-city / outstation state carriers
    outstation_keywords: Tuple[str, ...] = ("KSRTC", "NWKRTC", "KKRTC")

    # Filtering mode:
    # 'STRICT_URBAN': Exclude all routes with any stop > urban_cutoff_km or outstation keywords
    # 'SUBURBAN_COMMUTER': Include routes within suburban_cutoff_km without outstation keywords
    # 'ALL': Preserve all routes without filtering
    filtering_mode: str = "STRICT_URBAN"

    # Financial / Operational benchmark parameters (from BMTC FY 2024-25 reporting)
    # Cost per kilometer in INR (8304.7 paise = 83.05 INR/km)
    benchmark_cpkm_inr: float = 83.05

    # Transfer network settings (disabled by default in Milestone 2 to avoid unobserved assumptions)
    allow_synthetic_transfers: bool = False
    max_transfer_walk_distance_m: float = 150.0
    assumed_walking_speed_mps: float = 1.2

    def resolve_paths(self, base_path: Path) -> "PipelineConfig":
        """Return a copy of config with paths resolved against the provided base_path."""
        return PipelineConfig(
            repo_root=base_path,
            gtfs_dir=base_path / self.gtfs_dir,
            output_dir=base_path / self.output_dir,
            osm_pbf_path=base_path / self.osm_pbf_path,
            bmtc_kpis_path=base_path / self.bmtc_kpis_path,
            bmtc_divisions_path=base_path / self.bmtc_divisions_path,
            bengaluru_center_lat=self.bengaluru_center_lat,
            bengaluru_center_lon=self.bengaluru_center_lon,
            urban_cutoff_km=self.urban_cutoff_km,
            suburban_cutoff_km=self.suburban_cutoff_km,
            outstation_cutoff_km=self.outstation_cutoff_km,
            outstation_keywords=self.outstation_keywords,
            filtering_mode=self.filtering_mode,
            benchmark_cpkm_inr=self.benchmark_cpkm_inr,
            allow_synthetic_transfers=self.allow_synthetic_transfers,
            max_transfer_walk_distance_m=self.max_transfer_walk_distance_m,
            assumed_walking_speed_mps=self.assumed_walking_speed_mps,
        )
