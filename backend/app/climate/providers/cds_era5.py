"""Copernicus Climate Data Store (CDS) adapter for ERA5 single levels and ERA5-Land.

Requires a CDS account, acceptance of the dataset licence on the CDS website, the ``cdsapi``
package (optional dependency, ``pip install cdsapi``) and the key in ``SN_CDS_API_KEY``.
Downloads are NetCDF and are parsed by :mod:`app.climate.providers.netcdf`.

Not exercised against the live service in this build (the CDS host is not reachable from the build
environment); the request builder and the NetCDF parsing path are unit-tested.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.climate.providers.base import ClimateDataset, ClimateProvider, ClimateRequest
from app.climate.providers.netcdf import parse_netcdf
from app.core.config import get_settings
from app.core.errors import ProviderError

DATASETS = {
    "era5": ("reanalysis-era5-single-levels", "hourly", "ERA5 hourly single levels (0.25°)"),
    "era5_land": ("reanalysis-era5-land", "daily", "ERA5-Land hourly (0.1°)"),
}
VARIABLES = {
    "era5": ["2m_temperature", "2m_dewpoint_temperature", "surface_pressure", "10m_u_component_of_wind",
             "10m_v_component_of_wind", "surface_solar_radiation_downwards", "surface_thermal_radiation_downwards",
             "total_cloud_cover", "total_precipitation", "soil_temperature_level_1", "soil_temperature_level_2",
             "soil_temperature_level_3", "soil_temperature_level_4"],
    "era5_land": ["2m_temperature", "2m_dewpoint_temperature", "surface_pressure", "10m_u_component_of_wind",
                  "10m_v_component_of_wind", "surface_solar_radiation_downwards", "surface_thermal_radiation_downwards",
                  "total_precipitation", "soil_temperature_level_1", "soil_temperature_level_2",
                  "soil_temperature_level_3", "soil_temperature_level_4"],
}


def build_request(req: ClimateRequest, product: str = "era5", box_deg: float = 0.3) -> tuple[str, dict]:
    name, _acc, _label = DATASETS[product]
    years = [str(y) for y in range(req.start.year, req.end.year + 1)]
    return name, {
        "product_type": ["reanalysis"],
        "variable": VARIABLES[product],
        "year": years,
        "month": [f"{m:02d}" for m in range(1, 13)],
        "day": [f"{d:02d}" for d in range(1, 32)],
        "time": [f"{h:02d}:00" for h in range(24)],
        "area": [req.latitude + box_deg, req.longitude - box_deg, req.latitude - box_deg, req.longitude + box_deg],
        "data_format": "netcdf",
        "download_format": "unarchived",
    }


class CDSERA5Provider(ClimateProvider):
    name = "cds_era5"
    product = "Copernicus CDS ERA5 / ERA5-Land"
    requires_credentials = True

    def __init__(self, product: str = "era5"):
        self.product_key = product

    def is_configured(self) -> bool:
        if get_settings().cds_api_key is None:
            return False
        try:
            import cdsapi  # noqa: F401
        except ImportError:
            return False
        return True

    def fetch(self, request: ClimateRequest) -> ClimateDataset:
        s = get_settings()
        if s.cds_api_key is None:
            raise ProviderError(self.name, "SN_CDS_API_KEY not configured (see docs/data_sources.md)")
        try:
            import cdsapi
        except ImportError as e:
            raise ProviderError(self.name, "the optional 'cdsapi' package is not installed") from e
        name, body = build_request(request, request.model or self.product_key)
        client = cdsapi.Client(url=s.cds_api_url, key=s.cds_api_key.get_secret_value(), quiet=True)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "era5.nc"
            try:
                client.retrieve(name, body, str(target))
            except Exception as e:  # noqa: BLE001 - surface the provider message
                raise ProviderError(self.name, f"CDS retrieval failed: {e}", retryable=True) from e
            _, acc, label = DATASETS[request.model or self.product_key]
            ds = parse_netcdf(target, latitude=request.latitude, longitude=request.longitude, elevation_m=request.elevation_m,
                              accumulation=acc, product=label)
        ds.provenance.provider = self.name
        ds.provenance.endpoint = f"{s.cds_api_url} dataset={name}"
        return ds
