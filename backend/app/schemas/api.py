"""Typed request/response schemas for the REST API."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ProjectCreate(BaseModel):
    config: dict = Field(..., description="ProjectConfig JSON (site is required)")
    reference_site_id: str | None = Field(None, description="Attach a bundled reference weather dataset")
    geocode_source: str | None = None


class ProjectPatch(BaseModel):
    patch: dict = Field(..., description="Partial ProjectConfig; a new configuration version is created")
    note: str = ""
    default_dataset_id: str | None = None


class GeocodeRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=200)
    provider: Literal["open_meteo", "nominatim"] | None = None


class ClimateFetchRequest(BaseModel):
    project_id: str | None = None
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    elevation_m: float | None = None
    start: date
    end: date
    providers: list[Literal["open_meteo", "nasa_power", "cds_era5"]] | None = None
    model: str | None = None

    @model_validator(mode="after")
    def _period(self):
        if self.end < self.start:
            raise ValueError("end must not be before start")
        if (self.end - self.start).days > 366 * 31:
            raise ValueError("period longer than 31 years")
        if self.start.year < 1940:
            raise ValueError("reanalysis coverage starts in 1940")
        return self


class ReferenceDatasetRequest(BaseModel):
    project_id: str | None = None
    site_id: str


class SimulationRequest(BaseModel):
    project_id: str
    dataset_id: str | None = None
    scenarios: list[str] | None = None
    weights: dict[str, float] | None = None


class VariableSpec(BaseModel):
    path: str
    values: list[Any] | None = None
    low: float | None = None
    high: float | None = None
    integer: bool = False


class OptimisationRequest(BaseModel):
    project_id: str
    dataset_id: str | None = None
    mode: Literal["constrained", "pareto"] = "pareto"
    objective: Literal["facility_mwh", "co2e_t", "cooling_mwh", "pue"] = "facility_mwh"
    strategies: list[str] | None = Field(None, description="Strategies to enable (and use default variables for)")
    variables: list[VariableSpec] | None = None
    water_cap_m3: float | None = Field(None, ge=0)
    method: Literal["auto", "grid", "de"] = "auto"
    budget: int = Field(24, ge=1, le=400)
    base_on: Literal["S1", "project"] = "S1"
    patch: dict | None = None
    simulation_id: str | None = None
    scenario_id: str | None = None


class SensitivityRequest(BaseModel):
    simulation_id: str
    kind: Literal["tornado", "monte_carlo", "rank_stability"]
    scenario_id: str | None = None
    baseline: str = "S0"
    metric: str = "facility_mwh"
    n: int = Field(16, ge=2, le=500)
    n_category: int = Field(6, ge=2, le=100)
    by_category: bool = True
    compare: list[str] | None = None


class ReportRequest(BaseModel):
    simulation_id: str
    format: Literal["pdf", "xlsx", "json", "zip"] = "pdf"


class PsychroRequest(BaseModel):
    t_db_c: float = Field(..., ge=-60, le=80)
    rh_pct: float | None = Field(None, ge=0, le=100)
    t_dp_c: float | None = Field(None, ge=-80, le=60)
    elevation_m: float = 0.0
    p_pa: float | None = Field(None, ge=30000, le=110000)


class StackCalcRequest(BaseModel):
    heights_m: list[float] = Field(default_factory=lambda: [5, 10, 15, 20, 25, 30, 40, 50])
    t_inside_c: float = 36.0
    t_outside_c: float = 18.0
    inlet_area_m2: float = Field(40.0, gt=0)
    outlet_area_m2: float = Field(30.0, gt=0)
    discharge_coefficient: float = Field(0.6, gt=0.05, le=1.0)
    extra_loss_k: float = Field(2.0, ge=0)
    wind_ms: float = Field(0.0, ge=0, le=60)
    cp_inlet: float = 0.6
    cp_outlet: float = -0.5
    required_flow_m3s: float | None = Field(None, ge=0)
    elevation_m: float = 0.0


class EAHECalcRequest(BaseModel):
    n_pipes: int = Field(40, ge=1, le=5000)
    length_m: float = Field(60.0, gt=1, le=500)
    diameter_m: float = Field(0.4, gt=0.05, le=3)
    depth_m: float = Field(3.0, gt=0.3, le=20)
    spacing_m: float = Field(2.0, gt=0.1, le=50)
    flow_m3s: float = Field(40.0, gt=0)
    t_in_c: float = 32.0
    rh_pct: float = Field(40.0, ge=1, le=100)
    t_ground_c: float = 15.0
    soil_k: float = Field(1.3, gt=0)
    soil_rho: float = Field(1460.0, gt=0)
    soil_c: float = Field(880.0, gt=0)
    hours: int = Field(720, ge=1, le=8760)
    bend_k: float = Field(0.3, ge=0)
    sweep: Literal["none", "length", "diameter", "flow", "depth"] = "length"


class LayerSpec(BaseModel):
    material: str | None = None
    thickness_m: float = Field(..., gt=0, le=3)
    k: float | None = Field(None, gt=0)
    rho: float | None = Field(None, gt=0)
    c: float | None = Field(None, gt=0)


class EnvelopeCalcRequest(BaseModel):
    layers: list[LayerSpec]
    solar_absorptance: float = Field(0.6, ge=0, le=1)
    r_si: float = 0.13
    r_se: float = 0.04


class AirflowReqRequest(BaseModel):
    it_load_kw: float = Field(..., gt=0)
    delta_t_k: float = Field(12.0, gt=1, le=30)
    rack_count: int = Field(1000, ge=1)
    max_rack_kw: float | None = None
    t_supply_c: float = 24.0
    elevation_m: float = 0.0
