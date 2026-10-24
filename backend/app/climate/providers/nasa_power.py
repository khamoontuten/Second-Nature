"""NASA POWER hourly point API adapter (modeled data: MERRA-2 meteorology, CERES/FLASHFlux solar).

Documentation: https://power.larc.nasa.gov/docs/services/api/temporal/hourly/
* Spatial resolution: meteorology 0.5° × 0.625° (MERRA-2), solar ~1° — grid-cell averages, not stations.
* Hourly radiation parameters are hourly totals in Wh/m², i.e. numerically the mean W/m² over the hour.
* Fill value −999 → missing.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from app.climate.http import CachedClient
from app.climate.providers.base import ClimateDataset, ClimateProvider, ClimateRequest, Provenance, haversine_km, now_iso
from app.core.errors import ProviderError

URL = "https://power.larc.nasa.gov/api/temporal/hourly/point"
PARAMS = {
    "T2M": ("t_db_c", "°C", 1.0), "RH2M": ("rh_pct", "%", 1.0), "T2MDEW": ("t_dp_c", "°C", 1.0),
    "PS": ("p_pa", "kPa", 1000.0), "WS10M": ("wind_ms", "m/s", 1.0), "WD10M": ("wind_dir_deg", "°", 1.0),
    "ALLSKY_SFC_SW_DWN": ("ghi_wm2", "Wh/m²", 1.0), "ALLSKY_SFC_SW_DNI": ("dni_wm2", "Wh/m²", 1.0),
    "ALLSKY_SFC_SW_DIFF": ("dhi_wm2", "Wh/m²", 1.0), "ALLSKY_SFC_LW_DWN": ("ir_horiz_wm2", "W/m²", 1.0),
    "PRECTOTCORR": ("precip_mm", "mm/h", 1.0), "CLOUD_AMT": ("cloud_pct", "%", 1.0),
}


class NasaPowerProvider(ClimateProvider):
    name = "nasa_power"
    product = "NASA POWER hourly (MERRA-2 / CERES)"

    def __init__(self, client: CachedClient | None = None):
        self.client = client or CachedClient(self.name)

    def fetch(self, request: ClimateRequest) -> ClimateDataset:
        frames, metas, last = [], [], None
        for y in range(request.start.year, request.end.year + 1):
            start = max(request.start, date(y, 1, 1))
            end = min(request.end, date(y, 12, 31))
            params = {"parameters": ",".join(PARAMS), "community": "RE", "latitude": request.latitude,
                      "longitude": request.longitude, "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
                      "format": "JSON", "time-standard": "UTC"}
            body, meta = self.client.get_json(URL, params)
            try:
                param_block = body["properties"]["parameter"]
            except (KeyError, TypeError) as e:
                raise ProviderError(self.name, f"unexpected response structure: {str(body)[:200]}") from e
            fill = body.get("header", {}).get("fill_value", -999.0)
            data, idx = {}, None
            for src, (dst, _u, scale) in PARAMS.items():
                series = param_block.get(src)
                if not series:
                    continue
                keys = sorted(series)
                if idx is None:
                    idx = pd.DatetimeIndex(pd.to_datetime(keys, format="%Y%m%d%H"), name="time_utc").tz_localize("UTC")
                vals = np.array([series[k] for k in keys], dtype=float)
                vals[vals <= fill + 1e-6] = np.nan
                data[dst] = vals * scale
            frames.append(pd.DataFrame(data, index=idx))
            metas.append(meta)
            last = body
        df = pd.concat(frames).sort_index()
        df = df[~df.index.duplicated(keep="first")]
        coords = (last or {}).get("geometry", {}).get("coordinates", [None, None, None])
        lon, lat, elev = (coords + [None] * 3)[:3]
        prov = Provenance(
            provider=self.name, product=self.product, source_url="https://power.larc.nasa.gov/", endpoint=URL,
            retrieved_at=metas[-1]["retrieved_at"] if metas else now_iso(), requested_lat=request.latitude,
            requested_lon=request.longitude, actual_lat=lat, actual_lon=lon, actual_elevation_m=elev,
            site_elevation_m=request.elevation_m,
            distance_km=haversine_km(request.latitude, request.longitude, lat, lon) if lat is not None else None,
            elevation_difference_m=(request.elevation_m - elev) if (request.elevation_m is not None and elev is not None) else None,
            spatial_resolution="0.5° × 0.625° (MERRA-2 meteorology); ~1° (CERES solar)", temporal_resolution="hourly",
            units={v[0]: ("Pa" if v[0] == "p_pa" else v[1].replace("Wh/m²", "W/m² (hourly mean)")) for v in PARAMS.values()},
            timezone="UTC", reference_period=f"{df.index[0]} – {df.index[-1]}", data_nature="modeled",
            licence="NASA POWER data are freely available; cite the POWER Project (NASA LaRC)",
            citation="NASA Langley Research Center (LaRC) POWER Project, funded through the NASA Earth Science/Applied Science Program.",
            missing_pct={c: float(100 * df[c].isna().mean()) for c in df.columns},
            transformations=["PS kPa → Pa", "fill value −999 → NaN", "hourly radiation Wh/m² taken as mean W/m² over the hour"],
            variables=list(df.columns), notes=["Model/assimilation product on a coarse grid; not a site measurement."],
            extra={"requests": [{k: m[k] for k in ("endpoint", "cached", "retrieved_at", "attempts")} for m in metas]},
        )
        return ClimateDataset(df, prov)
