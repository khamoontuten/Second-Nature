"""Open-Meteo Historical Weather API adapter (reanalysis; ERA5 / ERA5-Land family).

Documentation: https://open-meteo.com/en/docs/historical-weather-api
* Data are reanalysis/model output, **not** station observations.
* Instantaneous variables are valid at the timestamp; radiation variables are averages over the
  *preceding* hour, so they are shifted −1 h to this platform's hour-beginning convention.
* Soil temperatures are model layers (0–7, 7–28, 28–100, 100–255 cm); they are not a measurement of
  the soil at the project site.
* Licence: Open-Meteo data CC BY 4.0; underlying ERA5 data © ECMWF / Copernicus (attribution required).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from app.climate.http import CachedClient
from app.climate.providers.base import ClimateDataset, ClimateProvider, ClimateRequest, Provenance, haversine_km, now_iso
from app.core.errors import ProviderError

URL = "https://archive-api.open-meteo.com/v1/archive"

VARIABLES = {
    "temperature_2m": ("t_db_c", "°C"),
    "relative_humidity_2m": ("rh_pct", "%"),
    "dew_point_2m": ("t_dp_c", "°C"),
    "surface_pressure": ("p_pa", "hPa"),
    "wind_speed_10m": ("wind_ms", "m/s"),
    "wind_direction_10m": ("wind_dir_deg", "°"),
    "wind_gusts_10m": ("wind_gust_ms", "m/s"),
    "shortwave_radiation": ("ghi_wm2", "W/m²"),
    "direct_normal_irradiance": ("dni_wm2", "W/m²"),
    "diffuse_radiation": ("dhi_wm2", "W/m²"),
    "cloud_cover": ("cloud_pct", "%"),
    "precipitation": ("precip_mm", "mm"),
    "soil_temperature_0_to_7cm": ("t_soil_0_07_c", "°C"),
    "soil_temperature_7_to_28cm": ("t_soil_07_28_c", "°C"),
    "soil_temperature_28_to_100cm": ("t_soil_28_100_c", "°C"),
    "soil_temperature_100_to_255cm": ("t_soil_100_255_c", "°C"),
}
SOIL_DEPTHS = {"t_soil_0_07_c": 0.035, "t_soil_07_28_c": 0.175, "t_soil_28_100_c": 0.64, "t_soil_100_255_c": 1.775}
RADIATION = {"ghi_wm2", "dni_wm2", "dhi_wm2"}
MODELS = {None: "best_match", "era5": "era5", "era5_land": "era5_land", "era5_seamless": "era5_seamless"}
RESOLUTION = {"era5": "0.25° (~25 km)", "era5_land": "0.1° (~9 km)", "best_match": "model-dependent (ERA5 0.25° / ERA5-Land 0.1°)",
              "era5_seamless": "0.1°/0.25° blend"}


class OpenMeteoProvider(ClimateProvider):
    name = "open_meteo"
    product = "Open-Meteo Historical Weather (reanalysis)"

    def __init__(self, client: CachedClient | None = None):
        self.client = client or CachedClient(self.name)

    def fetch(self, request: ClimateRequest) -> ClimateDataset:
        model = MODELS.get(request.model, request.model or "best_match")
        variables = list(VARIABLES)
        frames, metas, meta_body = [], [], None
        for y in range(request.start.year, request.end.year + 1):
            start = max(request.start, date(y, 1, 1))
            end = min(request.end, date(y, 12, 31))
            params = {"latitude": request.latitude, "longitude": request.longitude, "start_date": start.isoformat(),
                      "end_date": end.isoformat(), "hourly": ",".join(variables), "timezone": "GMT",
                      "wind_speed_unit": "ms", "models": model}
            body, meta = self.client.get_json(URL, params)
            if "hourly" not in body:
                raise ProviderError(self.name, f"unexpected response (no 'hourly'): {str(body)[:200]}")
            meta_body = body
            metas.append(meta)
            frames.append(self._frame(body))
        df = pd.concat(frames).sort_index()
        df = df[~df.index.duplicated(keep="first")]
        return self._dataset(df, meta_body, metas, request, model)

    def _frame(self, body: dict) -> pd.DataFrame:
        h = body["hourly"]
        idx = pd.DatetimeIndex(pd.to_datetime(h["time"]), name="time_utc").tz_localize("UTC")
        data = {}
        for src, (dst, _unit) in VARIABLES.items():
            if src in h:
                vals = np.array([np.nan if v is None else float(v) for v in h[src]])
                if src == "surface_pressure":
                    vals = vals * 100.0  # hPa → Pa
                data[dst] = vals
        df = pd.DataFrame(data, index=idx)
        for col in RADIATION & set(df.columns):
            df[col] = df[col].shift(-1)  # preceding-hour mean → hour-beginning convention
        return df

    def _dataset(self, df, body, metas, req: ClimateRequest, model: str) -> ClimateDataset:
        lat, lon, elev = body.get("latitude"), body.get("longitude"), body.get("elevation")
        prov = Provenance(
            provider=self.name, product=f"{self.product} [models={model}]", source_url="https://open-meteo.com/en/docs/historical-weather-api",
            endpoint=URL, retrieved_at=metas[-1]["retrieved_at"] if metas else now_iso(),
            requested_lat=req.latitude, requested_lon=req.longitude, actual_lat=lat, actual_lon=lon, actual_elevation_m=elev,
            site_elevation_m=req.elevation_m,
            distance_km=haversine_km(req.latitude, req.longitude, lat, lon) if lat is not None else None,
            elevation_difference_m=(req.elevation_m - elev) if (req.elevation_m is not None and elev is not None) else None,
            spatial_resolution=RESOLUTION.get(model, "model-dependent"), temporal_resolution="hourly",
            units={v[0]: ("Pa" if v[0] == "p_pa" else v[1]) for v in VARIABLES.values()}, timezone="UTC",
            reference_period=f"{df.index[0]} – {df.index[-1]}", data_nature="reanalysis",
            licence="CC BY 4.0 (Open-Meteo); ERA5 © ECMWF/Copernicus Climate Change Service",
            citation="Zippenfenig, P. Open-Meteo.com Weather API. Hersbach et al. (2020) ERA5; Muñoz-Sabater et al. (2021) ERA5-Land.",
            missing_pct={c: float(100 * df[c].isna().mean()) for c in df.columns},
            transformations=["surface pressure hPa → Pa", "radiation (preceding-hour means) shifted −1 h to hour-beginning",
                             "timestamps requested in GMT/UTC"],
            variables=list(df.columns), notes=["Reanalysis grid-cell values; not a measurement at the site.",
                                              "Soil temperature columns are model layers, not site soil measurements."],
            extra={"requests": [{k: m[k] for k in ("endpoint", "cached", "retrieved_at", "attempts")} for m in metas],
                   "model": model},
        )
        soil = {c: d for c, d in SOIL_DEPTHS.items() if c in df.columns and df[c].notna().mean() > 0.5}
        return ClimateDataset(df, prov, soil_layers_m=soil)
