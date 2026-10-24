"""Typed project configuration (Pydantic v2).

Every leaf value is traceable: fields the user did not set are reported by
``assumption_register`` as ``default-assumption``; fields the user set are ``user`` unless an
explicit provenance tag (``measured``, ``quoted``, ``derived``, ``user``) is supplied in
``ProjectConfig.sources`` under the field's dotted path.

Units are SI unless the field name says otherwise (e.g. ``_mw``, ``_kwh``, ``_pct``).
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SourceTag = Literal["user", "default-assumption", "measured", "quoted", "derived", "retrieved"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# ------------------------------------------------------------------------------------------ site
class EnvironmentalConstraints(_M):
    water_source: Literal["municipal_potable", "reclaimed", "groundwater", "surface", "mixed", "unknown"] = "municipal_potable"
    permitted_annual_withdrawal_m3: float | None = Field(None, ge=0, description="Hard cap on site water withdrawal")
    permitted_daily_withdrawal_m3: float | None = Field(None, ge=0)
    reclaimed_water_fraction: float = Field(0.0, ge=0, le=1)
    water_recycling_available: bool = False
    water_stress_score: float | None = Field(None, ge=0, le=5, description="WRI Aqueduct baseline water stress (0–5), basin level")
    water_stress_source: str | None = None
    groundwater_depth_m: float | None = Field(None, ge=0)
    groundwater_protection_zone: bool = False
    flood_exposure: Literal["low", "medium", "high", "unknown"] = "unknown"
    dust_exposure: Literal["low", "medium", "high", "unknown"] = "unknown"
    salinity_exposure: Literal["low", "medium", "high", "unknown"] = "unknown"
    gaseous_contamination_high: bool = False
    poor_air_quality_hours: list[int] = Field(default_factory=list, description="Hour indices where outdoor air must not be admitted")
    noise_limit_dba: float | None = None
    radon_risk: Literal["low", "medium", "high", "unknown"] = "unknown"
    permitting_notes: str = ""


class SiteConfig(_M):
    name: str = "Unnamed site"
    country: str = ""
    address: str = ""
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    elevation_m: float = Field(0.0, ge=-450, le=6000)
    timezone: str = "UTC"
    site_area_m2: float | None = Field(None, gt=0)
    land_available_for_buried_m2: float | None = Field(None, ge=0)
    terrain: Literal["large_city", "urban_suburban", "open", "flat_unobstructed"] = "urban_suburban"
    constraints: EnvironmentalConstraints = Field(default_factory=EnvironmentalConstraints)


# ------------------------------------------------------------------------------------------ IT
class ITLoadConfig(_M):
    installed_capacity_mw: float = Field(5.0, gt=0.01, le=500)
    utilisation_scenario: Literal["low", "expected", "peak", "ai_hpc", "custom"] = "expected"
    average_utilisation: float = Field(0.65, gt=0, le=1.0, description="Mean IT power / installed capacity")
    diurnal_amplitude: float = Field(0.05, ge=0, le=0.5, description="Peak-to-mean diurnal swing as fraction of mean")
    weekly_amplitude: float = Field(0.02, ge=0, le=0.5)
    annual_growth: float = Field(0.0, ge=-0.5, le=1.0)
    profile_hourly_fraction: list[float] | None = Field(None, description="Optional 8760 load fractions (of installed)")
    rack_count: int = Field(1000, ge=1)
    avg_rack_kw: float = Field(5.0, gt=0)
    max_rack_kw: float = Field(12.0, gt=0)
    server_delta_t_k: float = Field(12.0, gt=3, le=25, description="Air temperature rise across IT equipment")
    inlet_setpoint_c: float = Field(24.0, ge=10, le=35)
    ashrae_class: Literal["A1", "A2", "A3", "A4", "H1"] = "A1"
    envelope_mode: Literal["recommended", "allowable"] = "recommended"
    economiser_supply_margin_k: float = Field(1.0, ge=0, le=5, description="Safety margin below envelope limits")
    containment: Literal["none", "partial", "full"] = "partial"
    liquid_cooled_fraction: float = Field(0.0, ge=0, le=0.95, description="Share of IT heat captured by direct liquid cooling")
    liquid_supply_c: float = Field(32.0, ge=15, le=50)
    uptime_target_pct: float = Field(99.982, ge=90, le=100)
    design_life_years: int = Field(20, ge=1, le=60)

    @model_validator(mode="after")
    def _racks(self):
        if self.max_rack_kw < self.avg_rack_kw:
            raise ValueError("max_rack_kw must be ≥ avg_rack_kw")
        return self


class ElectricalConfig(_M):
    ups_capacity_factor: float = Field(1.25, ge=1.0, le=3.0)
    ups_efficiency_points: list[tuple[float, float]] | None = None
    distribution_loss_fraction: float = Field(0.015, ge=0, le=0.1)
    lighting_w_m2: float = Field(4.0, ge=0)
    lighting_on_fraction: float = Field(0.3, ge=0, le=1)
    other_fixed_kw: float = Field(50.0, ge=0)
    controls_fraction_of_it: float = Field(0.001, ge=0, le=0.02)
    losses_in_cooled_space_fraction: float = Field(1.0, ge=0, le=1)
    electrical_redundancy: Literal["N", "N+1", "2N", "2N+1"] = "2N"


# ------------------------------------------------------------------------------------------ building
class BuildingConfig(_M):
    length_m: float = Field(120.0, gt=1)
    width_m: float = Field(60.0, gt=1)
    height_m: float = Field(12.0, gt=2)
    floors: int = Field(1, ge=1, le=20)
    hall_floor_area_m2: float | None = Field(None, gt=0)
    orientation_deg: float = Field(0.0, ge=0, lt=360, description="Azimuth of the long-facade normal (0 = facing N)")
    window_area_m2: float = Field(0.0, ge=0)
    window_u: float = Field(1.6, gt=0)
    window_shgc: float = Field(0.35, ge=0, le=1)
    insulation_m: float = Field(0.10, ge=0, le=0.6)
    thermal_mass_m: float = Field(0.0, ge=0, le=0.6)
    roof_solar_reflectance: float = Field(0.30, ge=0, le=1)
    roof_emissivity: float = Field(0.90, ge=0, le=1)
    wall_solar_absorptance: float = Field(0.60, ge=0, le=1)
    thermal_bridge_factor: float = Field(1.15, ge=1, le=2)
    infiltration_ach: float = Field(0.05, ge=0, le=3)
    min_outdoor_air_m3s: float = Field(2.0, ge=0, description="Pressurisation / ventilation air treated by the plant")
    zone_air_offset_k: float = Field(3.0, description="Mean hall air temperature above supply (envelope interior BC)")

    @property
    def floor_area_m2(self) -> float:
        return self.hall_floor_area_m2 or self.length_m * self.width_m * self.floors

    @property
    def volume_m3(self) -> float:
        return self.length_m * self.width_m * self.height_m


# ------------------------------------------------------------------------------------------ plant
class BaselinePlantConfig(_M):
    architecture: Literal[
        "air_cooled_chiller_crah", "water_cooled_chiller_tower", "dx_crac", "dx_with_airside_economiser",
        "direct_evaporative", "indirect_evaporative", "liquid_cooling_dry_cooler",
    ] = "air_cooled_chiller_crah"
    chiller_model: Literal["carnot", "biquadratic", "constant"] = "carnot"
    rated_cop: float | None = Field(None, gt=0.5, le=15, description="AHRI full-load COP; default by architecture")
    capft: tuple[float, float, float, float, float, float] | None = None
    eirft: tuple[float, float, float, float, float, float] | None = None
    eirfplr: tuple[float, float, float] | None = None
    chw_supply_c: float = Field(7.0, ge=4, le=25, description="Conventional reference: 7 °C; improved designs raise it")
    chw_delta_t_k: float = Field(6.0, gt=1, le=15)
    cooling_redundancy: Literal["N", "N+1", "N+2", "2N"] = "N+1"
    n_units: int = Field(4, ge=1, le=64, description="Number of duty (N) cooling units")
    design_margin: float = Field(0.10, ge=0, le=0.5)
    crah_dp_pa: float = Field(350.0, gt=0)
    crah_fan_efficiency: float = Field(0.62, gt=0.1, le=0.95)
    run_redundant_fans: bool = True
    pump_head_pa: float = Field(250_000.0, gt=0)
    pump_efficiency: float = Field(0.70, gt=0.1, le=0.95)
    waterside_economiser: bool = False
    tower_approach_k: float = Field(4.0, gt=0.5, le=15)
    tower_cycles: float = Field(5.0, gt=1.1, le=20)
    tower_drift_fraction: float = Field(1e-5, ge=0, le=1e-3)
    dry_cooler_approach_k: float = Field(6.0, gt=1, le=20)
    economiser_fan_dp_pa: float = Field(250.0, gt=0)
    dec_effectiveness: float = Field(0.85, gt=0.3, le=1.0)
    iec_wet_bulb_effectiveness: float = Field(0.70, gt=0.2, le=1.0)
    humidification: Literal["adiabatic", "steam", "none"] = "adiabatic"
    dehumidification_allowed: bool = True


# ------------------------------------------------------------------------------------------ strategies
class StackConfig(_M):
    """Strategies A (termite mound) and E (solar chimney)."""

    enabled: bool = False
    chimney_height_m: float = Field(25.0, gt=0, le=150)
    chimney_area_m2: float = Field(30.0, gt=0)
    n_chimneys: int = Field(4, ge=1, le=200)
    inlet_area_m2_per_facade: dict[str, float] = Field(default_factory=lambda: {"N": 40.0, "E": 20.0, "S": 40.0, "W": 20.0})
    inlet_height_m: float = Field(1.5, ge=0)
    discharge_coefficient: float = Field(0.6, gt=0.1, le=1.0)
    chimney_loss_k: float = Field(1.5, ge=0, description="Entry + friction + exit losses referred to chimney area")
    inlet_path_loss_k: float = Field(2.0, ge=0, description="Louvres + filter + damper loss referred to inlet area")
    containment_leak_area_m2: float = Field(2.0, ge=0)
    chimney_cp: float = Field(-0.5, ge=-1.5, le=0.5, description="Wind pressure coefficient at the chimney outlet")
    solar_chimney: bool = False
    collector_area_m2: float = Field(200.0, ge=0)
    collector_efficiency: float = Field(0.4, ge=0, le=0.9)
    fan_assist: bool = True
    fan_efficiency: float = Field(0.6, gt=0.1, le=0.95)
    spiral_intakes: bool = False
    spiral_loss_reduction: float = Field(0.75, ge=0, le=0.95, description="Hypothesis: fraction of bend loss removed by spiral intakes")


class EnvelopeStrategyConfig(_M):
    """Strategy B (mollusc shell) — layered envelope; G (cool / radiative roof) via roof properties."""

    enabled: bool = False
    insulation_m: float = Field(0.20, ge=0, le=0.6)
    thermal_mass_m: float = Field(0.15, ge=0, le=0.6)
    roof_solar_reflectance: float = Field(0.85, ge=0, le=1)
    roof_emissivity: float = Field(0.90, ge=0, le=1)
    wall_solar_absorptance: float = Field(0.35, ge=0, le=1)


class FacadeConfig(_M):
    """Strategy C (fish scales): kinetic louvres, compared with fixed shading."""

    enabled: bool = False
    mode: Literal["kinetic", "fixed"] = "kinetic"
    facades: list[Literal["N", "E", "S", "W"]] = Field(default_factory=lambda: ["E", "W", "S"])
    overhang_pf: float = Field(0.5, ge=0, le=3)
    fin_pf: float = Field(0.3, ge=0, le=3)
    close_beam_threshold_wm2: float = Field(150.0, ge=0)
    beam_block_closed: float = Field(0.85, ge=0, le=1)
    gust_lock_ms: float = Field(20.0, gt=0)
    standby_w_per_panel: float = Field(0.5, ge=0)
    panel_area_m2: float = Field(2.0, gt=0)
    stuck_fraction: float = Field(0.0, ge=0, le=1)
    open_adds_inlet_area_m2: float = Field(0.0, ge=0, description="Extra natural-ventilation inlet area when panels open")


class EAHEConfig(_M):
    """Strategy D (prairie-dog burrow) earth-air heat exchanger."""

    enabled: bool = False
    n_pipes: int = Field(40, ge=1, le=5000)
    length_m: float = Field(60.0, gt=1, le=500)
    diameter_m: float = Field(0.40, gt=0.05, le=3)
    wall_thickness_m: float = Field(0.02, gt=0.001, le=0.3)
    pipe_k: float = Field(0.40, gt=0.01, le=60)
    depth_m: float = Field(3.0, gt=0.3, le=20)
    spacing_m: float = Field(2.0, gt=0.1, le=50)
    roughness_m: float = Field(5e-5, gt=0, le=0.01)
    n_bends: int = Field(2, ge=0, le=20)
    bend_k: float = Field(0.30, ge=0, le=3)
    filter_dp_design_pa: float = Field(120.0, ge=0)
    design_flow_m3s: float = Field(40.0, gt=0)
    fan_efficiency: float = Field(0.60, gt=0.1, le=0.95)
    soil_type: Literal["soil_generic", "clay", "sand_dry", "granite", "sand_moist", "loam_moist", "custom", "unknown"] = "soil_generic"
    soil_k: float | None = Field(None, gt=0, le=10)
    soil_rho: float | None = Field(None, gt=500, le=4000)
    soil_c: float | None = Field(None, gt=100, le=5000)
    measured_ground_temp_c: float | None = None
    measured_ground_depth_m: float | None = None
    surface_offset_k: float = Field(0.0, ge=-10, le=10)
    regeneration: bool = False
    operate_threshold_k: float = Field(1.0, ge=0, le=10, description="Run when outdoor exceeds ground by this much")
    years: int = Field(3, ge=1, le=20, description="Consecutive years simulated to capture soil drift")


class ThermalStorageConfig(_M):
    """Strategy F (thermal mass / TES)."""

    enabled: bool = False
    capacity_kwh_th: float = Field(10_000.0, ge=0)
    max_charge_kw: float = Field(1500.0, ge=0)
    max_discharge_kw: float = Field(1500.0, ge=0)
    standing_loss_per_h: float = Field(0.0005, ge=0, le=0.05)
    discharge_efficiency: float = Field(0.95, gt=0.3, le=1)
    pump_w_per_kw: float = Field(8.0, ge=0)


class RadiativeConfig(_M):
    """Strategy G (purpose-built radiative sky-cooling panels; cool roof lives in the envelope)."""

    enabled: bool = False
    panel_area_m2: float = Field(3000.0, ge=0)
    emissivity: float = Field(0.95, ge=0, le=1)
    solar_absorptance: float = Field(0.05, ge=0, le=1)
    soiling_derate: float = Field(0.85, ge=0, le=1)
    pump_w_per_m2: float = Field(0.5, ge=0)


class EvaporativeConfig(_M):
    """Strategy H (evaporation-assisted)."""

    enabled: bool = False
    kind: Literal["indirect", "direct", "precool_condenser"] = "indirect"
    effectiveness: float = Field(0.70, gt=0.2, le=1.0)
    cycles_of_concentration: float = Field(4.0, gt=1.1, le=20)


class EconomiserConfig(_M):
    """Air-side economiser (used by improved-conventional, low-water and hybrid scenarios)."""

    enabled: bool = False
    kind: Literal["direct", "indirect"] = "direct"
    indirect_effectiveness: float = Field(0.70, gt=0.2, le=0.95)
    path_dp_pa: float = Field(250.0, gt=0)


class ControllerConfig(_M):
    """Strategy I supervisory controller."""

    w_energy: float = Field(1.0, ge=0)
    w_carbon: float = Field(0.0, ge=0)
    w_water: float = Field(0.0, ge=0)
    w_cost: float = Field(0.0, ge=0)
    hysteresis: float = Field(0.05, ge=0, le=0.5, description="Relative objective improvement needed to switch")
    min_run_h: int = Field(2, ge=1, le=24)
    temp_deadband_k: float = Field(0.5, ge=0, le=3)


class StrategiesConfig(_M):
    stack: StackConfig = Field(default_factory=StackConfig)
    envelope: EnvelopeStrategyConfig = Field(default_factory=EnvelopeStrategyConfig)
    facade: FacadeConfig = Field(default_factory=FacadeConfig)
    eahe: EAHEConfig = Field(default_factory=EAHEConfig)
    storage: ThermalStorageConfig = Field(default_factory=ThermalStorageConfig)
    radiative: RadiativeConfig = Field(default_factory=RadiativeConfig)
    evaporative: EvaporativeConfig = Field(default_factory=EvaporativeConfig)
    economiser: EconomiserConfig = Field(default_factory=EconomiserConfig)
    controller: ControllerConfig = Field(default_factory=ControllerConfig)


# ------------------------------------------------------------------------------------------ grid / economics
class GridConfig(_M):
    emission_factor_kg_per_kwh: float | None = Field(None, ge=0, le=2.0)
    factor_type: Literal["average", "marginal"] = "average"
    accounting_basis: Literal["location", "market"] = "location"
    monthly_factors_kg_per_kwh: list[float] | None = Field(None, min_length=12, max_length=12)
    hourly_dataset_id: str | None = None
    marginal_factor_kg_per_kwh: float | None = Field(None, ge=0, le=2.0)
    contracted_renewable_fraction: float = Field(0.0, ge=0, le=1)
    source: str = "not provided"
    reference_year: int | None = None


class CostItem(_M):
    value: float | None = None
    source: Literal["quoted", "measured", "assumed", "missing"] = "missing"
    note: str = ""


class EconomicsConfig(_M):
    currency: str = "USD"
    reference_year: int = 2026
    electricity_price_per_kwh: float | None = Field(None, ge=0)
    water_price_per_m3: float | None = Field(None, ge=0)
    discount_rate: float = Field(0.07, ge=-0.05, le=0.5)
    horizon_years: int = Field(20, ge=1, le=60)
    capex: dict[str, CostItem] = Field(default_factory=dict, description="Keyed by strategy id")
    maintenance_per_year: dict[str, CostItem] = Field(default_factory=dict)
    embodied_factors_kgco2e_per_kg: dict[str, CostItem] = Field(default_factory=dict, description="Keyed by material")


class SimulationSettings(_M):
    eahe_substeps: int = Field(4, ge=1, le=12)
    envelope_substeps: int = Field(2, ge=1, le=12)
    warming_offset_k: float = Field(0.0, ge=-5, le=8, description="Uniform warming sensitivity test (not a projection)")
    closure_tolerance: float = Field(0.01, gt=0, le=0.1)
    year_filter: int | None = None


class ProjectConfig(_M):
    name: str = "Untitled project"
    description: str = ""
    site: SiteConfig
    it: ITLoadConfig = Field(default_factory=ITLoadConfig)
    electrical: ElectricalConfig = Field(default_factory=ElectricalConfig)
    building: BuildingConfig = Field(default_factory=BuildingConfig)
    baseline: BaselinePlantConfig = Field(default_factory=BaselinePlantConfig)
    strategies: StrategiesConfig = Field(default_factory=StrategiesConfig)
    grid: GridConfig = Field(default_factory=GridConfig)
    economics: EconomicsConfig = Field(default_factory=EconomicsConfig)
    simulation: SimulationSettings = Field(default_factory=SimulationSettings)
    sources: dict[str, SourceTag] = Field(default_factory=dict, description="Provenance tags keyed by dotted path")
    notes: dict[str, str] = Field(default_factory=dict)

    @field_validator("sources")
    @classmethod
    def _paths(cls, v):
        for k in v:
            if not k or " " in k:
                raise ValueError(f"invalid source path {k!r}")
        return v

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def config_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


def assumption_register(cfg: BaseModel, prefix: str = "", explicit: dict[str, str] | None = None) -> list[dict]:
    """List every leaf parameter with its value and provenance tag (user vs default-assumption)."""
    explicit = explicit if explicit is not None else getattr(cfg, "sources", {}) or {}
    rows: list[dict] = []
    for name, finfo in type(cfg).model_fields.items():
        if name in ("sources", "notes"):
            continue
        val = getattr(cfg, name)
        path = f"{prefix}{name}"
        if isinstance(val, BaseModel):
            sub = assumption_register(val, prefix=path + ".", explicit=explicit)
            if name not in cfg.model_fields_set:
                for r in sub:
                    if r["source"] == "user":
                        r["source"] = "default-assumption"
            rows += sub
            continue
        tag = explicit.get(path) or ("user" if name in cfg.model_fields_set else "default-assumption")
        rows.append({"path": path, "value": val if not isinstance(val, dict) else dict(val),
                     "source": tag, "description": finfo.description or ""})
    return rows
