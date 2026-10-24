"""Climate acquisition orchestration: provider order, fallbacks (recorded), reference datasets, QC."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.climate import quality
from app.climate.providers.base import ClimateDataset, ClimateProvider, ClimateRequest
from app.climate.providers.cds_era5 import CDSERA5Provider
from app.climate.providers.epw import EPWProvider
from app.climate.providers.nasa_power import NasaPowerProvider
from app.climate.providers.open_meteo import OpenMeteoProvider
from app.core.config import get_settings
from app.core.errors import NotFound, ProviderError

log = logging.getLogger("second_nature.climate")

PROVIDERS: dict[str, type[ClimateProvider]] = {
    "open_meteo": OpenMeteoProvider,
    "nasa_power": NasaPowerProvider,
    "cds_era5": CDSERA5Provider,
}

PROVIDER_INFO = {
    "open_meteo": {"label": "Open-Meteo Historical Weather (ERA5/ERA5-Land reanalysis)", "credentials": False,
                   "nature": "reanalysis", "url": "https://open-meteo.com/en/docs/historical-weather-api"},
    "nasa_power": {"label": "NASA POWER hourly (MERRA-2 / CERES)", "credentials": False, "nature": "modeled",
                   "url": "https://power.larc.nasa.gov/"},
    "cds_era5": {"label": "Copernicus CDS ERA5 / ERA5-Land", "credentials": True, "nature": "reanalysis",
                 "url": "https://cds.climate.copernicus.eu/"},
    "reference_epw": {"label": "Bundled reference TMY3 (NREL, via EnergyPlus)", "credentials": False, "nature": "typical_year",
                      "url": "https://github.com/NREL/EnergyPlus/tree/develop/weather"},
    "upload": {"label": "User upload (CSV, EPW, NetCDF)", "credentials": False, "nature": "as declared", "url": ""},
}


@dataclass
class FetchOutcome:
    dataset: ClimateDataset
    quality: quality.QualityReport
    provider_used: str
    attempts: list[dict]


def reference_catalog() -> dict:
    path = get_settings().reference_dir / "weather" / "catalog.json"
    return json.loads(path.read_text())


def load_reference(site_id: str, *, site_lat: float | None = None, site_lon: float | None = None,
                   site_elevation_m: float | None = None) -> ClimateDataset:
    cat = reference_catalog()
    entry = next((s for s in cat["sites"] if s["id"] == site_id), None)
    if entry is None:
        raise NotFound(f"reference weather site {site_id!r} not found")
    path = Path(get_settings().reference_dir) / "weather" / (entry["file"] + ".gz")
    prov = EPWProvider(path, source_url=cat["raw_url_template"].format(file=entry["file"]), licence=cat["licence"],
                       product=f"NREL TMY3 — {entry['label']} (WMO {entry['wmo']})", citation=cat["dataset_citation"])
    ds = prov.fetch(ClimateRequest(site_lat or entry["latitude"], site_lon or entry["longitude"], date(2001, 1, 1),
                                   date(2001, 12, 31), elevation_m=site_elevation_m if site_elevation_m is not None else entry["elevation_m"]))
    ds.provenance.provider = "reference_epw"
    ds.provenance.notes.append("Typical meteorological year (TMY3): composite of representative months; understates extremes.")
    return ds


def fetch_climate(request: ClimateRequest, order: list[str] | None = None, providers: dict[str, ClimateProvider] | None = None
                  ) -> FetchOutcome:
    """Try providers in order. Any substitution is recorded in provenance.fallback_from; nothing is silent."""
    order = order or get_settings().climate_provider_order
    attempts = []
    for name in order:
        prov = (providers or {}).get(name) or PROVIDERS[name]()
        if not prov.is_configured():
            attempts.append({"provider": name, "status": "skipped", "reason": "not configured (credentials or package missing)"})
            continue
        try:
            ds = prov.fetch(request)
        except ProviderError as e:
            log.warning(f"provider {name} failed: {e.message}")
            attempts.append({"provider": name, "status": "failed", "reason": e.message})
            continue
        ds.provenance.fallback_from = [a["provider"] for a in attempts if a["status"] == "failed"]
        if ds.provenance.fallback_from:
            ds.provenance.notes.append("Primary provider(s) failed; this dataset is a recorded fallback: "
                                       + "; ".join(f"{a['provider']}: {a['reason']}" for a in attempts))
        attempts.append({"provider": name, "status": "ok"})
        ds = quality.reindex_hourly(ds)
        rep = quality.assess(ds, request.longitude)
        ds.provenance.quality_flags = {k: v for k, v in rep.out_of_range.items() if v}
        return FetchOutcome(ds, rep, name, attempts)
    raise ProviderError("climate", "all providers failed or were unavailable: "
                        + "; ".join(f"{a['provider']} ({a['status']}: {a.get('reason', '')})" for a in attempts),
                        details={"attempts": attempts})
