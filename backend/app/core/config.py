"""Environment-based configuration. Secrets are read from the environment only and never logged."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(REPO_ROOT / ".env", ".env"), env_prefix="SN_", extra="ignore")

    app_name: str = "Second Nature"
    environment: str = "development"
    log_level: str = "INFO"

    # Database: SQLite by default for a zero-dependency workstation install;
    # PostgreSQL (+PostGIS) in docker-compose / production.
    database_url: str = Field(default=f"sqlite:///{(REPO_ROOT / 'data' / 'second_nature.db').as_posix()}")

    # Directory for Parquet datasets, simulation outputs, caches and generated reports.
    data_dir: Path = REPO_ROOT / "data"
    reference_dir: Path = REPO_ROOT / "data" / "reference"

    # HTTP behaviour for external providers.
    http_timeout_s: float = 60.0
    http_max_retries: int = 4
    http_backoff_base_s: float = 1.0
    http_user_agent: str = "SecondNature/0.1 (engineering decision support; contact: set SN_CONTACT_EMAIL)"
    contact_email: str | None = None

    # Provider order for climate acquisition (first available wins; fallbacks are recorded).
    climate_provider_order: list[str] = ["open_meteo", "nasa_power"]

    # Optional credentials (server side only).
    electricity_maps_api_key: SecretStr | None = None
    cds_api_url: str = "https://cds.climate.copernicus.eu/api"
    cds_api_key: SecretStr | None = None

    # Geocoding.
    geocoder: str = "open_meteo"  # "nominatim" | "open_meteo"
    nominatim_url: str = "https://nominatim.openstreetmap.org/search"

    # Uploads.
    max_upload_mb: int = 50
    allowed_upload_suffixes: list[str] = [".csv", ".epw", ".nc", ".nc4", ".txt"]

    # Jobs.
    job_workers: int = 2

    # CORS for the frontend dev server.
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Rate limiting (requests per minute per client for expensive endpoints).
    rate_limit_per_minute: int = 120

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def datasets_dir(self) -> Path:
        return self.data_dir / "processed" / "climate"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def results_dir(self) -> Path:
        return self.data_dir / "processed" / "results"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.cache_dir, self.datasets_dir, self.raw_dir, self.results_dir, self.reports_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
