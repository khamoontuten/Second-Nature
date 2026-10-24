"""Climate feature engineering and summaries (all computed from the dataset; nothing assumed).

Heatwave definition used here (data-driven, documented): ≥ 3 consecutive local days whose daily maximum
dry-bulb exceeds the dataset's 95th percentile of daily maxima. Design conditions are the dataset's
0.4 / 1 / 2 % annual exceedance values (dry bulb, wet bulb, dew point) — computed from the supplied
record, which for typical-year (TMY) data understates true extremes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.climate.providers.base import ClimateDataset
from app.physics import psychrometrics as ps

SECTORS = 16
SPEED_BINS = [0, 1, 2, 4, 6, 8, 10, 15, 100]


def derived_frame(ds: ClimateDataset, elevation_m: float = 0.0) -> pd.DataFrame:
    """Canonical data plus psychrometric derivations (wet bulb, humidity ratio, enthalpy, density)."""
    df = ds.data.copy()
    p = df["p_pa"].to_numpy() if "p_pa" in df and df["p_pa"].notna().mean() > 0.9 else np.full(len(df), float(ps.pressure_at_elevation(elevation_m)))
    p = np.where(np.isfinite(p), p, float(ps.pressure_at_elevation(elevation_m)))
    t = df["t_db_c"].to_numpy(dtype=float)
    if "t_dp_c" in df and df["t_dp_c"].notna().mean() > 0.5:
        w = ps.humidity_ratio_from_dew_point(np.minimum(df["t_dp_c"].to_numpy(dtype=float), t), p)
    elif "rh_pct" in df:
        w = ps.humidity_ratio_from_rh(t, np.clip(df["rh_pct"].to_numpy(dtype=float), 0.5, 100), p)
    else:
        w = np.full(len(df), np.nan)
    ok = np.isfinite(t) & np.isfinite(w)
    df["w_kgkg"] = w
    df["t_wb_c"] = np.where(ok, ps.wet_bulb_from_humidity_ratio(np.where(ok, t, 20), np.where(ok, w, 0.01), p), np.nan)
    df["h_kjkg"] = ps.enthalpy(t, w) / 1000.0
    df["rho_kgm3"] = ps.moist_air_density(t, w, p)
    if "t_dp_c" not in df or df["t_dp_c"].isna().all():
        df["t_dp_c"] = np.where(ok, ps.dew_point_from_humidity_ratio(np.where(ok, w, 0.01), p), np.nan)
    if "rh_pct" not in df or df["rh_pct"].isna().all():
        df["rh_pct"] = np.where(ok, ps.rh_from_humidity_ratio(t, w, p), np.nan)
    return df


def _stats(s: pd.Series) -> dict:
    s = s.dropna()
    if s.empty:
        return {}
    q = s.quantile([0.05, 0.5, 0.95, 0.99])
    return {"mean": float(s.mean()), "median": float(q[0.5]), "min": float(s.min()), "max": float(s.max()),
            "p05": float(q[0.05]), "p50": float(q[0.5]), "p95": float(q[0.95]), "p99": float(q[0.99]), "n": int(len(s))}


def _longest_run(mask: pd.Series) -> int:
    if not mask.any():
        return 0
    grp = (~mask).cumsum()
    return int(mask.groupby(grp).sum().max())


def heatwaves(daily_max: pd.Series, min_days: int = 3) -> dict:
    thr = float(daily_max.quantile(0.95))
    hot = daily_max > thr
    events, run, start = [], 0, None
    for d, v in hot.items():
        if v:
            run += 1
            start = start or d
        else:
            if run >= min_days:
                events.append({"start": str(start), "days": run})
            run, start = 0, None
    if run >= min_days:
        events.append({"start": str(start), "days": run})
    return {"threshold_c": thr, "definition": f"≥{min_days} consecutive days with daily max > 95th percentile of daily maxima",
            "count": len(events), "max_duration_days": max((e["days"] for e in events), default=0), "events": events}


def wind_rose(df: pd.DataFrame) -> dict:
    if "wind_dir_deg" not in df or "wind_ms" not in df:
        return {}
    d = df[["wind_dir_deg", "wind_ms"]].dropna()
    sector = ((d["wind_dir_deg"] + 360 / SECTORS / 2) % 360 // (360 / SECTORS)).astype(int)
    speed_bin = pd.cut(d["wind_ms"], SPEED_BINS, right=False, labels=False)
    table = pd.crosstab(sector, speed_bin).reindex(index=range(SECTORS), columns=range(len(SPEED_BINS) - 1), fill_value=0)
    freq = 100.0 * table / max(len(d), 1)
    labels = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    prevailing = labels[int(table.sum(axis=1).idxmax())]
    return {"sectors": labels, "speed_bins_ms": SPEED_BINS, "frequency_pct": freq.round(3).values.tolist(),
            "prevailing": prevailing, "calm_pct": float(100.0 * (d["wind_ms"] < 0.5).mean())}


def summarise_climate(ds: ClimateDataset, *, elevation_m: float = 0.0, timezone: str = "UTC", free_cooling_threshold_c: float = 24.0,
                      hot_threshold_c: float = 32.0) -> dict:
    df = derived_frame(ds, elevation_m)
    try:
        local = df.index.tz_convert(timezone)
    except Exception:  # noqa: BLE001
        local = df.index
    df_local = df.copy()
    df_local.index = local
    variables = [c for c in ("t_db_c", "t_dp_c", "t_wb_c", "rh_pct", "w_kgkg", "h_kjkg", "wind_ms", "ghi_wm2", "dni_wm2",
                             "dhi_wm2", "cloud_pct", "p_pa", "precip_mm") if c in df]
    overall = {v: _stats(df[v]) for v in variables}
    month = local.month
    monthly = {v: {stat: df[v].groupby(month).agg(stat).round(4).tolist() for stat in ("mean", "median", "min", "max")}
               for v in variables}
    years = sorted(set(local.year))
    annual_extremes = {int(y): {"t_max_c": float(df["t_db_c"][local.year == y].max()), "t_min_c": float(df["t_db_c"][local.year == y].min())}
                       for y in years}
    n = len(df)
    design = {}
    for v in ("t_db_c", "t_wb_c", "t_dp_c"):
        if v in df:
            design[v] = {f"{p}%": float(df[v].quantile(1 - p / 100)) for p in (0.4, 1.0, 2.0)}
    t = df["t_db_c"]
    daily_max = df_local["t_db_c"].resample("D").max().dropna()
    fc = t <= free_cooling_threshold_c
    hot = t >= hot_threshold_c
    hot_humid = (t >= 30.0) & (df["t_dp_c"] >= 20.0) if "t_dp_c" in df else pd.Series(False, index=df.index)
    solar = None
    if "ghi_wm2" in df:
        solar = df_local["ghi_wm2"].groupby([local.month, local.hour]).mean().unstack(fill_value=0.0).round(2)
        solar = {"months": solar.index.tolist(), "hours": solar.columns.tolist(), "mean_ghi_wm2": solar.values.tolist(),
                 "annual_kwh_m2": float(df["ghi_wm2"].sum() / 1000.0 * (8760 / max(n, 1)))}
    rain = None
    if "precip_mm" in df and df["precip_mm"].notna().mean() > 0.5:
        rain = {"monthly_mm": df_local["precip_mm"].groupby(local.month).sum().round(2).tolist(),
                "annual_mm": float(df["precip_mm"].sum() * 8760 / max(n, 1)), "max_hourly_mm": float(df["precip_mm"].max())}
    hist = {}
    for v, bins in (("t_db_c", np.arange(-30, 51, 2)), ("rh_pct", np.arange(0, 101, 5)), ("t_dp_c", np.arange(-35, 33, 2)),
                    ("t_wb_c", np.arange(-30, 36, 2)), ("wind_ms", np.arange(0, 26, 1))):
        if v in df:
            counts, edges = np.histogram(df[v].dropna(), bins=bins)
            hist[v] = {"edges": edges.tolist(), "counts": counts.tolist()}
    soil = {c: _stats(df[c]) | {"monthly_mean": df[c].groupby(month).mean().round(3).tolist(), "depth_m": d}
            for c, d in ds.soil_layers_m.items() if c in df}
    return {
        "period": {"start_utc": str(df.index[0]), "end_utc": str(df.index[-1]), "hours": n, "years": years,
                   "data_nature": ds.provenance.data_nature},
        "completeness_pct": {v: float(100 * df[v].notna().mean()) for v in df.columns if v in ds.data.columns},
        "overall": overall, "monthly": monthly, "annual_extremes": annual_extremes, "design_conditions": design,
        "free_cooling_hours_below_threshold": {"threshold_c": free_cooling_threshold_c, "hours": int(fc.sum()),
                                               "longest_run_h": _longest_run(fc)},
        "hot_hours": {"threshold_c": hot_threshold_c, "hours": int(hot.sum()), "longest_run_h": _longest_run(hot)},
        "hot_humid_hours": {"definition": "T_db ≥ 30 °C and T_dp ≥ 20 °C", "hours": int(hot_humid.sum()),
                            "longest_run_h": _longest_run(hot_humid)},
        "heatwaves": heatwaves(daily_max) if len(daily_max) > 20 else {},
        "wind_rose": wind_rose(df), "solar": solar, "rainfall": rain, "histograms": hist, "soil_layers": soil,
        "monthly_ground_temperatures_epw_modeled": {str(k): v for k, v in ds.monthly_ground_c.items()},
    }


def coincident_peak_events(it_power_w: np.ndarray, t_db: np.ndarray, t_dp: np.ndarray, it_quantile: float = 0.9,
                           t_quantile: float = 0.95) -> dict:
    """Hours when IT load and adverse climate peak together (relevant for plant sizing and risk)."""
    it_hi = it_power_w >= np.quantile(it_power_w, it_quantile)
    t_hi = t_db >= np.quantile(t_db, t_quantile)
    dp_hi = t_dp >= np.quantile(t_dp, t_quantile)
    return {"hot_and_high_load_h": int(np.sum(it_hi & t_hi)), "humid_and_high_load_h": int(np.sum(it_hi & dp_hi)),
            "definition": f"IT ≥ {int(it_quantile * 100)}th pct and T_db (or T_dp) ≥ {int(t_quantile * 100)}th pct"}
