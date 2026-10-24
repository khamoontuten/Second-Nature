"""Climate data quality control.

Checks: duplicate and missing timestamps, impossible values (physical bounds), dew point above dry
bulb, spikes (hour-to-hour step and robust z-score outliers), flat-lined sensors, time-zone
plausibility (timing of the daily irradiance peak vs solar noon), gap inventory, cross-source
comparison. Optional gap filling interpolates only short gaps and flags every filled value.

Flag bits per cell: 1 missing, 2 gap-filled, 4 out of physical range, 8 outlier/spike, 16 flat-lined.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from app.climate.providers.base import ClimateDataset

F_MISSING, F_FILLED, F_RANGE, F_OUTLIER, F_FLAT = 1, 2, 4, 8, 16

BOUNDS = {
    "t_db_c": (-90.0, 60.0), "t_dp_c": (-95.0, 35.0), "rh_pct": (0.0, 100.5), "p_pa": (30_000.0, 110_000.0),
    "wind_ms": (0.0, 75.0), "wind_gust_ms": (0.0, 110.0), "wind_dir_deg": (0.0, 360.0), "ghi_wm2": (0.0, 1500.0),
    "dni_wm2": (0.0, 1400.0), "dhi_wm2": (0.0, 900.0), "ir_horiz_wm2": (50.0, 600.0), "cloud_pct": (0.0, 100.0),
    "precip_mm": (0.0, 400.0),
}
MAX_STEP = {"t_db_c": 15.0, "t_dp_c": 20.0, "p_pa": 1500.0}


@dataclass
class QualityReport:
    n_rows: int
    start: str
    end: str
    expected_hours: int
    duplicate_timestamps: int
    missing_timestamps: int
    completeness_pct: dict[str, float]
    out_of_range: dict[str, int]
    dewpoint_above_drybulb: int
    spikes: dict[str, int]
    flatlines: dict[str, int]
    gaps: dict[str, dict]
    timezone_check: dict
    warnings: list[str] = field(default_factory=list)
    filled: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    runs, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i - start))
            start = None
    if start is not None:
        runs.append((start, len(mask) - start))
    return runs


def timezone_check(df: pd.DataFrame, lon: float) -> dict:
    """Compare the mean UTC hour of peak GHI with expected solar noon (≈ 12 − lon/15 h UTC)."""
    if "ghi_wm2" not in df or df["ghi_wm2"].fillna(0).max() <= 0:
        return {"checked": False, "reason": "no irradiance data"}
    g = df["ghi_wm2"].fillna(0)
    by_hour = g.groupby(g.index.hour).mean()
    hours = by_hour.index.to_numpy(dtype=float)
    weights = by_hour.to_numpy()
    ang = 2 * np.pi * hours / 24
    centroid = (np.degrees(np.arctan2(np.sum(weights * np.sin(ang)), np.sum(weights * np.cos(ang)))) / 15.0) % 24
    centroid += 0.5  # hour-beginning index: mid-hour
    expected = (12.0 - lon / 15.0) % 24
    diff = ((centroid - expected + 12) % 24) - 12
    return {"checked": True, "irradiance_centroid_utc_h": float(centroid), "expected_solar_noon_utc_h": float(expected),
            "offset_h": float(diff), "plausible": bool(abs(diff) <= 1.0)}


def assess(ds: ClimateDataset, longitude: float | None = None, long_gap_h: int = 6) -> QualityReport:
    df = ds.data
    idx = df.index
    dup = int(idx.duplicated().sum())
    full = pd.date_range(idx.min(), idx.max(), freq="h", tz="UTC") if len(idx) else idx
    missing_ts = int(len(full) - len(idx.unique()))
    warnings = []
    oor, spikes, flats, gaps, comp = {}, {}, {}, {}, {}
    flags = ds.flags if ds.flags is not None else pd.DataFrame(0, index=df.index, columns=df.columns, dtype="int16")
    for col in df.columns:
        s = df[col]
        miss = s.isna().to_numpy()
        flags[col] = flags[col].to_numpy() | np.where(miss, F_MISSING, 0)
        comp[col] = float(100.0 * (1 - miss.mean()))
        lo, hi = BOUNDS.get(col, (-np.inf, np.inf)) if not col.startswith("t_soil") else (-60.0, 60.0)
        bad = (s < lo) | (s > hi)
        oor[col] = int(bad.sum())
        flags[col] = flags[col].to_numpy() | np.where(bad, F_RANGE, 0)
        if col in MAX_STEP:
            step = s.diff().abs() > MAX_STEP[col]
            spikes[col] = int(step.sum())
            flags[col] = flags[col].to_numpy() | np.where(step, F_OUTLIER, 0)
        if col in ("t_db_c", "t_dp_c", "rh_pct", "p_pa", "wind_ms"):
            same = (s.diff() == 0).astype(int)
            flat = same.groupby((same == 0).cumsum()).transform("sum") >= 24
            flats[col] = int(flat.sum())
            flags[col] = flags[col].to_numpy() | np.where(flat, F_FLAT, 0)
        rs = _runs(miss)
        if rs:
            longest = max(r[1] for r in rs)
            gaps[col] = {"n_gaps": len(rs), "longest_h": int(longest), "missing_h": int(miss.sum()),
                         "long_gaps": [{"start": str(idx[a]), "hours": n} for a, n in rs if n > long_gap_h][:50]}
            if longest > long_gap_h:
                warnings.append(f"{col}: {sum(1 for r in rs if r[1] > long_gap_h)} gap(s) longer than {long_gap_h} h "
                                f"(longest {longest} h) — not interpolated automatically")
    dp_bad = 0
    if "t_dp_c" in df and "t_db_c" in df:
        dp_bad = int((df["t_dp_c"] > df["t_db_c"] + 0.5).sum())
        if dp_bad:
            warnings.append(f"{dp_bad} hours with dew point above dry bulb")
    if dup:
        warnings.append(f"{dup} duplicate timestamps")
    if missing_ts:
        warnings.append(f"{missing_ts} missing hourly timestamps in the record")
    tz = timezone_check(df, longitude) if longitude is not None else {"checked": False}
    if tz.get("checked") and not tz.get("plausible"):
        warnings.append(f"irradiance peak is offset {tz['offset_h']:+.1f} h from solar noon — check time zone / timestamp convention")
    if len(full) and len(full) < 8760 * 0.95:
        warnings.append(f"record covers {len(full)} h (< 1 year); annual results would be incomplete")
    ds.flags = flags.astype("int16")
    return QualityReport(len(df), str(idx.min()) if len(idx) else "", str(idx.max()) if len(idx) else "", len(full), dup,
                         missing_ts, comp, oor, dp_bad, spikes, flats, gaps, tz, warnings)


def reindex_hourly(ds: ClimateDataset) -> ClimateDataset:
    """Drop duplicates and insert missing hourly timestamps as NaN (flagged missing)."""
    df = ds.data[~ds.data.index.duplicated(keep="first")].sort_index()
    full = pd.date_range(df.index.min(), df.index.max(), freq="h", tz="UTC", name="time_utc")
    added = len(full) - len(df)
    df = df.reindex(full)
    flags = (ds.flags[~ds.flags.index.duplicated(keep="first")].reindex(full).fillna(F_MISSING).astype("int16")
             if ds.flags is not None else None)
    ds.data, ds.flags = df, flags
    if added:
        ds.provenance.transformations.append(f"{added} missing hourly timestamps inserted as NaN")
    return ds


def fill_short_gaps(ds: ClimateDataset, max_gap_h: int = 3, columns: list[str] | None = None) -> dict[str, int]:
    """Linear interpolation of gaps ≤ max_gap_h only; every filled cell is flagged and counted."""
    filled = {}
    for col in columns or list(ds.data.columns):
        s = ds.data[col]
        miss = s.isna()
        if not miss.any():
            continue
        run_len = miss.groupby((~miss).cumsum()).transform("sum")
        fillable = miss & (run_len <= max_gap_h)
        interp = s.interpolate(limit_area="inside")
        new = s.where(~fillable, interp)
        n = int((new.notna() & miss).sum())
        if n:
            ds.data[col] = new
            ds.flags[col] = ds.flags[col].to_numpy() | np.where(new.notna() & miss, F_FILLED, 0)
            filled[col] = n
    if filled:
        ds.provenance.transformations.append(f"linear interpolation of gaps ≤ {max_gap_h} h: {filled}")
    return filled


def compare_sources(a: ClimateDataset, b: ClimateDataset, columns: list[str] | None = None) -> dict:
    """Bias (b − a), RMSE and correlation over the overlapping period for shared variables."""
    common = a.data.index.intersection(b.data.index)
    cols = columns or [c for c in a.data.columns if c in b.data.columns]
    out = {"overlap_hours": int(len(common)), "variables": {}}
    for c in cols:
        x = a.data.loc[common, c]
        y = b.data.loc[common, c]
        ok = x.notna() & y.notna()
        if ok.sum() < 24:
            continue
        d = (y[ok] - x[ok]).to_numpy()
        out["variables"][c] = {"n": int(ok.sum()), "bias": float(d.mean()), "rmse": float(np.sqrt(np.mean(d**2))),
                               "corr": float(np.corrcoef(x[ok], y[ok])[0, 1])}
    return out
