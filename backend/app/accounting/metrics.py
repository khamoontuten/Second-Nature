"""Energy, water, carbon and heat accounting with consistent boundaries.

PUE = Σ E_facility / Σ E_IT over the same period and boundary (annual ratio of energies).
The arithmetic mean of hourly PUE values is reported separately and labeled as such.
WUE (site) = Σ site water / Σ E_IT  [L/kWh_IT], given for withdrawal and for consumption.
Carbon: Σ E_facility,t · I_t with the stated factor type (average or marginal) and basis.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.cooling import hybrid
from app.physics import psychrometrics as ps

KWH = 1.0 / 1000.0  # W·h → kWh for hourly steps


def _e(series) -> float:
    """Energy (kWh) of an hourly power series (W)."""
    return float(np.nansum(series)) * KWH


def free_cooling_eligibility(hourly: pd.DataFrame, ctx, hs) -> dict:
    """Environmental eligibility of outdoor air (separate from actual useful cooling delivered)."""
    t_oa = ctx.t_db
    t_ret = hourly["t_return_c"].to_numpy()
    w_hi, w_lo = hybrid.humidity_limits(hs.envelope, np.clip(t_oa, hs.t_set, hs.t_econ_max) + hs.recirc_k, ctx.p)
    dry_bulb_ok = t_oa <= hs.t_econ_max
    h_ret = ps.enthalpy(t_ret, hourly["w_supply"].to_numpy())
    enthalpy_ok = ctx.h < h_ret
    humid_hi = ctx.w > w_hi
    humid_lo = ctx.w < w_lo
    aq_bad = hs.poor_aq
    full_env = dry_bulb_ok & ~humid_hi & ~humid_lo & ~aq_bad
    n = len(t_oa)
    return {
        "dry_bulb_eligible_h": int(dry_bulb_ok.sum()),
        "enthalpy_eligible_h": int(enthalpy_ok.sum()),
        "full_envelope_eligible_h": int(full_env.sum()),
        "rejected_temperature_h": int((~dry_bulb_ok).sum()),
        "rejected_humidity_high_h": int((dry_bulb_ok & humid_hi).sum()),
        "rejected_humidity_low_h": int((dry_bulb_ok & humid_lo).sum()),
        "rejected_air_quality_h": int((dry_bulb_ok & aq_bad).sum()),
        "hours": n,
        "full_envelope_eligible_pct": 100.0 * full_env.sum() / n,
        "_masks": {"dry_bulb": dry_bulb_ok, "enthalpy": enthalpy_ok, "full": full_env, "humid_hi": humid_hi, "humid_lo": humid_lo},
    }


def summarise(cfg, hourly: pd.DataFrame, ctx, modes, sel, sz, env_loads, eahe_res, tes_info, closure_rep, failure,
              intensity_label: str) -> dict:
    h = hourly
    n = len(h)
    years = n / 8760.0
    e_it = _e(h["p_it_w"])
    e_fac = _e(h["p_facility_w"])
    e_cool = _e(h["p_cooling_w"])
    comp = {c.replace("_w", ""): _e(h[c]) / 1000.0 for c in h.columns
            if c.startswith("p_") and c.endswith("_w") and c not in ("p_facility_w", "p_cooling_w")}
    pue_hourly = h["p_facility_w"] / h["p_it_w"].clip(lower=1.0)
    local = pd.DatetimeIndex(h["time_local"])
    month = local.month
    monthly = pd.DataFrame({
        "it_mwh": h["p_it_w"].groupby(month).sum().to_numpy() / 1e6,
        "facility_mwh": h["p_facility_w"].groupby(month).sum().to_numpy() / 1e6,
        "cooling_mwh": h["p_cooling_w"].groupby(month).sum().to_numpy() / 1e6,
        "water_m3": h["wl_withdrawal"].groupby(month).sum().to_numpy() / 1000.0,
        "co2e_t": h["co2e_kg"].groupby(month).sum().to_numpy() / 1000.0,
        "envelope_mwh": h["q_envelope_w"].groupby(month).sum().to_numpy() / 1e6,
        "free_cooling_h": ((h["mode"] != "mechanical") & (h["mode"] != "mech_precool") & ~h["partial"]).groupby(month).sum().to_numpy(),
        "partial_h": (h["partial"] & (h["mode"] != "mechanical")).groupby(month).sum().to_numpy(),
        "mechanical_h": ((h["mode"] == "mechanical") | (h["mode"] == "mech_precool")).groupby(month).sum().to_numpy(),
        "residual_mech_cooling_mwh": h["q_coil_w"].groupby(month).sum().to_numpy() / 1e6,
    }, index=sorted(set(month)))
    monthly["pue"] = monthly["facility_mwh"] / monthly["it_mwh"]
    water_w = float(h["wl_withdrawal"].sum()) / 1000.0
    water_c = float(h["wl_consumption"].sum()) / 1000.0
    reclaimed = cfg.site.constraints.reclaimed_water_fraction
    co2 = float(np.nansum(h["co2e_kg"])) / 1000.0 if np.isfinite(h["carbon_intensity_kg_kwh"]).any() else None
    co2_cool = (float(np.nansum(h["p_cooling_w"] / 1000.0 * h["carbon_intensity_kg_kwh"])) / 1000.0) if co2 is not None else None
    marginal = None
    if cfg.grid.marginal_factor_kg_per_kwh is not None:
        marginal = e_fac * cfg.grid.marginal_factor_kg_per_kwh / 1000.0
    mode_hours = h["mode"].value_counts().reindex(hybrid.MODES, fill_value=0).to_dict()
    mode_energy = {m: _e(h.loc[h["mode"] == m, "p_cooling_w"]) / 1000.0 for m in hybrid.MODES}
    free_full = int(((~h["mode"].isin(["mechanical", "mech_precool"])) & ~h["partial"]).sum())
    partial = int(((~h["mode"].isin(["mechanical", "mech_precool"])) & h["partial"]).sum())
    hs_proxy = _HSProxy(cfg, h, ctx, modes)
    elig = free_cooling_eligibility(h, ctx, hs_proxy.hs)
    elig.pop("_masks")
    heat_paths = {k: _e(h[c]) / 1000.0 for k, c in [
        ("plant_condenser", "q_rejected_condenser_w"), ("economiser_exhaust", "q_exhaust_w"), ("exfiltration", "q_exfiltration_w"),
        ("ground_eahe", "q_ground_w"), ("iec_secondary_air", "q_iec_secondary_w"), ("radiative_sky", "q_radiative_w"),
        ("liquid_loop", "q_liquid_rejected_w"), ("free_economiser_waterside", "q_free_economiser_w"), ("unmet", "unmet_w")]}
    price_e = cfg.economics.electricity_price_per_kwh
    price_w = cfg.economics.water_price_per_m3
    out = {
        "period": {"start_utc": str(h.index[0]), "end_utc": str(h.index[-1]), "hours": n, "years": years},
        "energy_mwh": {"it": e_it / 1000, "facility": e_fac / 1000, "cooling": e_cool / 1000, "components": comp,
                       "by_mode_cooling": mode_energy},
        "pue": {"annual": e_fac / max(e_it, 1e-9), "mean_of_hourly": float(pue_hourly.mean()),
                "cooling_partial_pue": (e_it + e_cool) / max(e_it, 1e-9), "max_hourly": float(pue_hourly.max()),
                "monthly": monthly["pue"].round(4).tolist()},
        "power_kw": {"peak_facility": float(h["p_facility_w"].max() / 1000), "peak_cooling": float(h["p_cooling_w"].max() / 1000),
                     "mean_it": float(h["p_it_w"].mean() / 1000), "peak_it": float(h["p_it_w"].max() / 1000),
                     "peak_plant_coil_load": float(h["q_coil_w"].max() / 1000)},
        "water": {"withdrawal_m3": water_w, "consumption_m3": water_c, "potable_m3": water_w * (1 - reclaimed),
                  "reclaimed_m3": water_w * reclaimed, "reclaimed_fraction": reclaimed,
                  "peak_hourly_withdrawal_m3": float(h["wl_withdrawal"].max() / 1000),
                  "peak_daily_withdrawal_m3": float(h["wl_withdrawal"].groupby(local.date).sum().max() / 1000),
                  "wue_withdrawal_l_per_kwh": water_w * 1000 / max(e_it, 1e-9),
                  "wue_consumption_l_per_kwh": water_c * 1000 / max(e_it, 1e-9),
                  "components_m3": {k: float(h[k].sum() / 1000) for k in hybrid.WATER_KEYS},
                  "stress_score": cfg.site.constraints.water_stress_score,
                  "stress_source": cfg.site.constraints.water_stress_source,
                  "boundary": "site operational water (cooling + humidification); excludes water embedded in electricity generation"},
        "carbon": {"operational_t": co2, "cooling_related_t": co2_cool, "intensity_basis": intensity_label,
                   "factor_type": cfg.grid.factor_type, "marginal_scenario_t": marginal, "source": cfg.grid.source,
                   "embodied_note": "embodied carbon reported separately (lifecycle module)"},
        "heat_mwh": {"it_heat": e_it / 1000, "envelope_net_gain": _e(h["q_envelope_w"]) / 1000, "paths": heat_paths,
                     "closure": closure_rep.to_dict(),
                     "envelope_storage_change": env_loads.stored_change_j / 3.6e9, "tes": tes_info},
        "free_cooling": {"full_hours": free_full, "partial_hours": partial,
                         "mechanical_hours": int(h["mode"].isin(["mechanical", "mech_precool"]).sum()),
                         "full_pct": 100 * free_full / n, "residual_mechanical_cooling_mwh": _e(h["q_coil_w"]) / 1000,
                         "eligibility": elig},
        "modes": {"hours": {k: int(v) for k, v in mode_hours.items()}, "switches": sel.switches,
                  "emergency_hours": sel.emergency_hours, "water_limited_hours": sel.water_limited_hours},
        "thermal": {"unmet_hours": int((h["unmet_w"] > 1).sum()), "max_inlet_c": float(h["t_inlet_c"].max()),
                    "min_inlet_c": float(h["t_inlet_c"].min()), "max_inlet_rh_pct": float(h["rh_inlet_pct"].max()),
                    "max_inlet_dp_c": float(h["dp_inlet_c"].max()),
                    "inlet_envelope_violation_hours": int(_violations(h, hs_proxy.hs).sum())},
        "costs": {"electricity": e_fac * price_e if price_e is not None else None,
                  "water": water_w * price_w if price_w is not None else None,
                  "currency": cfg.economics.currency,
                  "basis": "operating cost only; capital cost reported in lifecycle module if supplied"},
        "monthly": monthly.reset_index(names="month").to_dict(orient="list"),
        "failure": failure,
        "sizing": {"q_design_kw": sz.q_design_w / 1e3, "units": f"{sz.n_duty} duty / {sz.n_installed} installed",
                   "unit_capacity_kw": sz.unit_capacity_w / 1e3, "n_duty": sz.n_duty, "n_installed": sz.n_installed,
                   "installed_capacity_kw": sz.unit_capacity_w * sz.n_installed / 1e3, "rated_cop": sz.chiller.rated_cop,
                   "design_airflow_m3s": sz.v_fac_design_m3s,
                   "crah_installed_airflow_m3s": sz.crah.design_flow_m3s_per_unit * sz.crah.n_units_installed},
    }
    if eahe_res is not None:
        on = h.get("eahe_flow_m3s", pd.Series(0, index=h.index)) > 0
        q = h.get("eahe_q_sensible_w", pd.Series(0.0, index=h.index))
        out["eahe"] = {
            "sensible_cooling_mwh": float(np.clip(q, 0, None).sum() / 1e6),
            "sensible_heating_mwh": float(np.clip(-q, 0, None).sum() / 1e6),
            "useful_hours": int((h["mode"] == "econ_eahe").sum()),
            "operating_hours": int(on.sum()),
            "peak_cooling_kw": float(q.max() / 1000), "mean_cooling_when_on_kw": float(q[on].mean() / 1000) if on.any() else 0.0,
            "mean_temperature_drop_k": float((h["t_oa_c"] - h["eahe_t_out_c"])[on].mean()) if on.any() else 0.0,
            "fan_energy_mwh": float(h["p_eahe_fans_w"].sum() / 1e6),
            "condensate_m3": float(h["eahe_condensate_kg"].sum() / 1000),
            "max_soil_drift_k": float(np.abs(h["eahe_soil_drift_k"]).max()),
            "yearly": eahe_res.yearly, "footprint_m2": eahe_res.diagnostics.get("footprint_m2"),
        }
    return out


def _violations(h, hs):
    env = hs.envelope
    t = h["t_inlet_c"].to_numpy()
    over_t = t > env.t_max_c + 1e-6
    under_t = t < env.t_min_c - 1e-6
    rh = h["rh_inlet_pct"].to_numpy()
    dp = h["dp_inlet_c"].to_numpy()
    over_h = (rh > env.rh_max_pct + 0.5) | (dp > env.dp_max_c + 0.1)
    return over_t | under_t | over_h | (h["unmet_w"].to_numpy() > 1)


class _HSProxy:
    """Rebuild the minimal hall state needed for eligibility statistics from the ledger."""

    def __init__(self, cfg, h, ctx, modes):
        from app.core.environmental_classes import get_envelope
        from app.simulation.engine import CONTAINMENT
        env = get_envelope(cfg.it.ashrae_class, cfg.it.envelope_mode, cfg.site.constraints.gaseous_contamination_high)
        c = CONTAINMENT[cfg.it.containment]
        t_max = env.derated_t_max(cfg.site.elevation_m)
        poor = np.zeros(len(h), bool)
        for i in cfg.site.constraints.poor_air_quality_hours:
            if 0 <= i < len(h):
                poor[i] = True
        self.hs = hybrid.HallState(h["q_it_air_w"].to_numpy(), h["q_space_losses_w"].to_numpy(), h["q_envelope_w"].to_numpy(),
                                   h["m_server_kg_s"].to_numpy(), h["m_facility_kg_s"].to_numpy(), np.zeros(len(h)),
                                   cfg.it.inlet_setpoint_c - c["recirc_k"], t_max - cfg.it.economiser_supply_margin_k - c["recirc_k"],
                                   env.t_min_c, c["recirc_k"], env, cfg.site.elevation_m, poor)
