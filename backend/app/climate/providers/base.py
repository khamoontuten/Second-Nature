"""Provider-independent climate data interface and provenance record."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Literal

import pandas as pd

DataNature = Literal["observed", "reanalysis", "typical_year", "forecast", "modeled", "manual", "mixed"]

CANONICAL_COLUMNS = [
    "t_db_c", "t_dp_c", "rh_pct", "p_pa", "wind_ms", "wind_dir_deg", "wind_gust_ms",
    "ghi_wm2", "dni_wm2", "dhi_wm2", "ir_horiz_wm2", "cloud_pct", "precip_mm",
]


@dataclass
class Provenance:
    provider: str
    product: str
    source_url: str
    endpoint: str | None
    retrieved_at: str
    requested_lat: float
    requested_lon: float
    actual_lat: float | None
    actual_lon: float | None
    actual_elevation_m: float | None
    site_elevation_m: float | None
    distance_km: float | None
    elevation_difference_m: float | None
    spatial_resolution: str
    temporal_resolution: str
    units: dict[str, str]
    timezone: str
    reference_period: str
    data_nature: DataNature
    licence: str
    citation: str
    missing_pct: dict[str, float] = field(default_factory=dict)
    quality_flags: dict[str, int] = field(default_factory=dict)
    transformations: list[str] = field(default_factory=list)
    fallback_from: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    station_id: str | None = None
    variables: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ClimateRequest:
    latitude: float
    longitude: float
    start: date
    end: date
    elevation_m: float | None = None
    variables: list[str] | None = None
    model: str | None = None  # provider-specific product selector (e.g., "era5", "era5_land")


@dataclass
class ClimateDataset:
    """Canonical hourly climate table (UTC index) + per-cell quality flags + provenance."""

    data: pd.DataFrame
    provenance: Provenance
    flags: pd.DataFrame | None = None
    soil_layers_m: dict[str, float] = field(default_factory=dict)     # column -> representative depth
    monthly_ground_c: dict[float, list[float]] = field(default_factory=dict)  # depth -> 12 monthly values (EPW, modeled)

    def __post_init__(self):
        idx = self.data.index
        if not isinstance(idx, pd.DatetimeIndex) or idx.tz is None or str(idx.tz) != "UTC":
            raise ValueError("Climate data must have a timezone-aware UTC DatetimeIndex")
        if self.flags is None:
            self.flags = pd.DataFrame(0, index=self.data.index, columns=self.data.columns, dtype="int16")


class ClimateProvider(ABC):
    name: str = "abstract"
    product: str = ""
    requires_credentials: bool = False

    @abstractmethod
    def fetch(self, request: ClimateRequest) -> ClimateDataset:  # pragma: no cover - interface
        ...

    def is_configured(self) -> bool:
        return True


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
