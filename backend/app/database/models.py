"""Relational data model. Large hourly series live in Parquet files referenced by path + SHA-256.

PostgreSQL + PostGIS in production: the migration adds a ``geography(Point, 4326)`` column and GiST
index on ``sites`` when the PostGIS extension is available. SQLite is used for local development and
tests (latitude/longitude columns are always present).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON}


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    version: Mapped[str] = mapped_column(String(32), unique=True)
    app_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    notes: Mapped[str] = mapped_column(Text, default="")


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    current_version_no: Mapped[int] = mapped_column(Integer, default=1)
    default_dataset_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("climate_datasets.id", use_alter=True), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    site: Mapped[Site | None] = relationship(back_populates="project", uselist=False, cascade="all, delete-orphan")
    versions: Mapped[list[ProjectConfigVersion]] = relationship(back_populates="project", cascade="all, delete-orphan",
                                                                order_by="ProjectConfigVersion.version_no")


class Site(Base):
    __tablename__ = "sites"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    country: Mapped[str] = mapped_column(String(100), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    elevation_m: Mapped[float] = mapped_column(Float, default=0.0)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    site_area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    geocode_source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    project: Mapped[Project] = relationship(back_populates="site")


class ProjectConfigVersion(Base):
    __tablename__ = "project_config_versions"
    __table_args__ = (UniqueConstraint("project_id", "version_no"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    version_no: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict] = mapped_column(JSON)
    config_hash: Mapped[str] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    project: Mapped[Project] = relationship(back_populates="versions")


class BuildingGeometry(Base):
    __tablename__ = "building_geometries"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    config_version_id: Mapped[str] = mapped_column(ForeignKey("project_config_versions.id", ondelete="CASCADE"))
    length_m: Mapped[float] = mapped_column(Float)
    width_m: Mapped[float] = mapped_column(Float)
    height_m: Mapped[float] = mapped_column(Float)
    floor_area_m2: Mapped[float] = mapped_column(Float)
    details: Mapped[dict] = mapped_column(JSON)


class ClimateProviderRecord(Base):
    __tablename__ = "climate_providers"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    label: Mapped[str] = mapped_column(String(200))
    requires_credentials: Mapped[bool] = mapped_column(Boolean, default=False)
    data_nature: Mapped[str] = mapped_column(String(40))
    url: Mapped[str] = mapped_column(Text, default="")


class ClimateDatasetRecord(Base):
    __tablename__ = "climate_datasets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    provider: Mapped[str] = mapped_column(String(40))
    product: Mapped[str] = mapped_column(Text)
    data_nature: Mapped[str] = mapped_column(String(40))
    start_utc: Mapped[str] = mapped_column(String(40))
    end_utc: Mapped[str] = mapped_column(String(40))
    n_hours: Mapped[int] = mapped_column(Integer)
    requested_lat: Mapped[float] = mapped_column(Float)
    requested_lon: Mapped[float] = mapped_column(Float)
    actual_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    provenance: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ClimateObservationSummary(Base):
    """Hourly observations are in Parquet; this table indexes per-variable coverage for queries."""

    __tablename__ = "climate_observations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("climate_datasets.id", ondelete="CASCADE"))
    variable: Mapped[str] = mapped_column(String(40))
    unit: Mapped[str] = mapped_column(String(40))
    completeness_pct: Mapped[float] = mapped_column(Float)
    mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    minimum: Mapped[float | None] = mapped_column(Float, nullable=True)
    maximum: Mapped[float | None] = mapped_column(Float, nullable=True)


class DataQualityReportRecord(Base):
    __tablename__ = "data_quality_reports"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("climate_datasets.id", ondelete="CASCADE"))
    report: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SoilParameters(Base):
    __tablename__ = "soil_parameters"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    soil_type: Mapped[str] = mapped_column(String(40))
    k_w_mk: Mapped[float | None] = mapped_column(Float, nullable=True)
    rho_kg_m3: Mapped[float | None] = mapped_column(Float, nullable=True)
    c_j_kgk: Mapped[float | None] = mapped_column(Float, nullable=True)
    measured_temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    measured_depth_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    groundwater_depth_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="user")
    reference: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class GridEmissionsDataset(Base):
    __tablename__ = "grid_emissions_datasets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    provider: Mapped[str] = mapped_column(String(40))
    zone: Mapped[str] = mapped_column(String(60), default="")
    factor_kind: Mapped[str] = mapped_column(String(60))
    path: Mapped[str] = mapped_column(Text)
    n_hours: Mapped[int] = mapped_column(Integer)
    mean_kg_per_kwh: Mapped[float] = mapped_column(Float)
    provenance: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WaterResourceDataset(Base):
    __tablename__ = "water_resource_datasets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    source: Mapped[str] = mapped_column(String(200))
    stress_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    details: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class EquipmentSpec(Base):
    __tablename__ = "equipment_specs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(60))
    manufacturer: Mapped[str] = mapped_column(String(200), default="")
    curves: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(40), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CoolingStrategyRecord(Base):
    __tablename__ = "cooling_strategies"
    id: Mapped[str] = mapped_column(String(8), primary_key=True)
    key: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    meta: Mapped[dict] = mapped_column(JSON)


class SimulationRun(Base):
    __tablename__ = "simulation_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    config_version_id: Mapped[str] = mapped_column(ForeignKey("project_config_versions.id"))
    dataset_id: Mapped[str] = mapped_column(ForeignKey("climate_datasets.id"))
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    model_version: Mapped[str] = mapped_column(String(32))
    config_hash: Mapped[str] = mapped_column(String(64))
    dataset_sha256: Mapped[str] = mapped_column(String(64))
    scenarios: Mapped[list] = mapped_column(JSON)
    comparison: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    recommendation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    hybrid_search: Mapped[list | None] = mapped_column(JSON, nullable=True)
    climate_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    weights: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    results_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    results: Mapped[list[ScenarioResult]] = relationship(back_populates="run", cascade="all, delete-orphan")


class SimulationInput(Base):
    __tablename__ = "simulation_inputs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("simulation_runs.id", ondelete="CASCADE"))
    config: Mapped[dict] = mapped_column(JSON)
    options: Mapped[dict] = mapped_column(JSON)
    assumption_register: Mapped[list] = mapped_column(JSON)


class ScenarioResult(Base):
    """Annual scenario summary (key metrics as columns, full summary as JSON) + hourly Parquet reference."""

    __tablename__ = "scenario_results"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("simulation_runs.id", ondelete="CASCADE"))
    scenario_id: Mapped[str] = mapped_column(String(8))
    label: Mapped[str] = mapped_column(String(200))
    strategies: Mapped[list] = mapped_column(JSON)
    pue: Mapped[float] = mapped_column(Float)
    facility_mwh: Mapped[float] = mapped_column(Float)
    cooling_mwh: Mapped[float] = mapped_column(Float)
    water_withdrawal_m3: Mapped[float] = mapped_column(Float)
    co2e_t: Mapped[float | None] = mapped_column(Float, nullable=True)
    unmet_hours: Mapped[int] = mapped_column(Integer)
    hourly_path: Mapped[str] = mapped_column(Text)
    hourly_sha256: Mapped[str] = mapped_column(String(64))
    summary: Mapped[dict] = mapped_column(JSON)
    diagnostics: Mapped[dict] = mapped_column(JSON)
    flags: Mapped[list] = mapped_column(JSON)
    config: Mapped[dict] = mapped_column(JSON)
    inputs_hash: Mapped[str] = mapped_column(String(64))
    run: Mapped[SimulationRun] = relationship(back_populates="results")


class OptimizationRun(Base):
    __tablename__ = "optimization_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    dataset_id: Mapped[str] = mapped_column(String(32))
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    request: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SensitivityResult(Base):
    __tablename__ = "sensitivity_results"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    simulation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(20))
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    request: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    simulation_id: Mapped[str] = mapped_column(ForeignKey("simulation_runs.id", ondelete="CASCADE"))
    format: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SourceProvenance(Base):
    __tablename__ = "source_provenance"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(32))
    provenance: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|succeeded|failed|cancelled
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
