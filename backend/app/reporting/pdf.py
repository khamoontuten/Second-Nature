"""PDF technical report (ReportLab). All figures and tables are generated from the stored simulation."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.reporting import charts
from app.reporting.data import LIMITATIONS, REFERENCES, ReportData
from app.strategies.library import STRATEGIES

# DejaVu Sans (bundled with matplotlib) covers the engineering symbols used in the report (CO₂, Δ, η, →, √, ṁ).
_FONT_DIR = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf"
pdfmetrics.registerFont(TTFont("DejaVu", str(_FONT_DIR / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(_FONT_DIR / "DejaVuSans-Bold.ttf")))
pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold", italic="DejaVu", boldItalic="DejaVu-Bold")

SS = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=SS["Heading1"], fontName="DejaVu-Bold", fontSize=15, spaceAfter=6, textColor=colors.HexColor("#1F4E79"))
H2 = ParagraphStyle("h2", parent=SS["Heading2"], fontName="DejaVu-Bold", fontSize=11.5, spaceBefore=8, spaceAfter=4,
                    textColor=colors.HexColor("#1F4E79"))
B = ParagraphStyle("b", parent=SS["BodyText"], fontName="DejaVu", fontSize=8.5, leading=11, alignment=TA_LEFT)
SMALL = ParagraphStyle("s", parent=B, fontSize=7, leading=9, textColor=colors.HexColor("#444444"))
TS = TableStyle([("FONT", (0, 0), (-1, -1), "DejaVu", 6.8), ("FONT", (0, 0), (-1, 0), "DejaVu-Bold", 6.8),
                 ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE6F1")), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                 ("VALIGN", (0, 0), (-1, -1), "TOP")])


def _p(text, style=B):
    return Paragraph(str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), style)


def _table(rows, widths=None):
    data = [[_p(c, SMALL) if isinstance(c, str) and len(c) > 25 else c for c in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TS)
    return t


def _img(png: bytes, width=170 * mm):
    img = Image(io.BytesIO(png))
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth, img.drawHeight = width, width * ratio
    return img


def _f(x, nd=0):
    if x is None:
        return "n/a"
    if isinstance(x, float) and not np.isfinite(x):
        return "n/a"
    return f"{x:,.{nd}f}"


def build_pdf(d: ReportData) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"Second Nature report — {d.project.name}", author="Second Nature")
    run, comp = d.run, d.run.comparison or {}
    cfg = d.inputs.config
    base_id, best_id = d.baseline_id, d.best_id
    base, best = d.results[base_id].summary, d.results[best_id].summary
    rec = run.recommendation or {}
    clim = run.climate_summary or {}
    prov = d.dataset.provenance
    story = []

    # Title + executive summary
    story += [Paragraph("Second Nature — Biomimetic Cooling Design Report", H1),
              _p(f"Project: <b>{d.project.name}</b> · Site: {cfg['site']['name']} ({cfg['site']['latitude']:.3f}, "
                 f"{cfg['site']['longitude']:.3f}, {cfg['site']['elevation_m']:.0f} m) · Generated "
                 f"{datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}".replace("<b>", "").replace("</b>", "")),
              _p(f"Model version {run.model_version} · configuration hash {run.config_hash[:16]} · climate dataset SHA-256 "
                 f"{d.dataset.sha256[:16]} · simulation {run.id}", SMALL),
              Spacer(1, 4),
              _p("Status: preliminary design and decision-support output. Not validated for construction and not certified for "
                 "regulatory compliance. See Limitations.", SMALL),
              Paragraph("1. Executive summary", H2)]
    d_e = 100 * (best["energy_mwh"]["facility"] - base["energy_mwh"]["facility"]) / base["energy_mwh"]["facility"]
    story.append(_p(
        f"Of the scenarios simulated on identical climate, IT load ({cfg['it']['installed_capacity_mw']} MW installed, "
        f"{best['energy_mwh']['it']:.0f} MWh/yr IT energy) and redundancy ({cfg['baseline']['cooling_redundancy']}), the highest "
        f"weighted objective was <b>{best_id} — {d.results[best_id].label}</b> ({' + '.join(d.results[best_id].strategies) or 'no added strategies'}). "
        f"Annual PUE {base['pue']['annual']:.3f} → {best['pue']['annual']:.3f}; facility energy {d_e:+.1f} %; "
        f"site water {base['water']['withdrawal_m3']:,.0f} → {best['water']['withdrawal_m3']:,.0f} m³/yr; "
        f"operational CO₂e {_f(base['carbon']['operational_t'])} → {_f(best['carbon']['operational_t'])} t/yr "
        f"({best['carbon']['intensity_basis']}). Unmet-cooling hours: {best['thermal']['unmet_hours']}; inlet-envelope "
        f"violations: {best['thermal']['inlet_envelope_violation_hours']}.".replace("<b>", "").replace("</b>", "")))
    mc = next((sres.result for sres in d.sensitivity if sres.kind == "monte_carlo" and sres.result), None)
    if mc and "facility_mwh" in mc.get("metrics", {}):
        sv = mc["metrics"]["facility_mwh"]["saving"]
        story.append(_p(f"Uncertainty (Monte Carlo, n = {mc['n']}): facility-energy saving {sv['p50']:,.0f} MWh/yr "
                        f"(P5 {sv['p5']:,.0f} – P95 {sv['p95']:,.0f}); probability the design beats the baseline "
                        f"{100 * (mc['metrics']['facility_mwh']['prob_design_better'] or 0):.0f} %."))
    else:
        story.append(_p("Uncertainty: no Monte Carlo analysis stored for this simulation; ranges below are qualitative. Run the "
                        "uncertainty analysis before relying on point estimates.", SMALL))
    if rec.get("ranking"):
        top = [f"{k} ({rec['candidates'][k]['explanation']['verdict'].lower()})" for k in rec["ranking"][:4]]
        story.append(_p("Strategy ranking (each strategy simulated on top of S1, hard constraints first): " + "; ".join(top) + "."))

    # 2 Site conditions
    story.append(Paragraph("2. Site conditions and climate", H2))
    ov = clim.get("overall", {})
    rows = [["Variable", "Mean", "P5", "P50", "P95", "P99", "Min", "Max"]]
    for v, label in (("t_db_c", "Dry bulb °C"), ("t_wb_c", "Wet bulb °C"), ("t_dp_c", "Dew point °C"), ("rh_pct", "RH %"),
                     ("wind_ms", "Wind m/s"), ("ghi_wm2", "GHI W/m²")):
        if v in ov and ov[v]:
            o = ov[v]
            rows.append([label] + [_f(o[k], 1) for k in ("mean", "p05", "p50", "p95", "p99", "min", "max")])
    story.append(_table(rows))
    dc = clim.get("design_conditions", {})
    hw = clim.get("heatwaves", {})
    story.append(_p(f"Dataset design conditions (0.4 % exceedance): dry bulb {_f(dc.get('t_db_c', {}).get('0.4%'), 1)} °C, wet bulb "
                    f"{_f(dc.get('t_wb_c', {}).get('0.4%'), 1)} °C, dew point {_f(dc.get('t_dp_c', {}).get('0.4%'), 1)} °C. Heatwaves "
                    f"({hw.get('definition', '')}): {hw.get('count', 'n/a')}, longest {hw.get('max_duration_days', 'n/a')} days. Hot-and-humid "
                    f"hours: {clim.get('hot_humid_hours', {}).get('hours', 'n/a')}. Prevailing wind: {clim.get('wind_rose', {}).get('prevailing', 'n/a')}."))
    try:
        h0 = d.hourly(base_id)
        t_local = pd.Series(h0["t_oa_c"].to_numpy(), index=pd.DatetimeIndex(h0["time_local"]))
        story.append(_img(charts.temperature_profile(t_local, "Outdoor dry-bulb temperature (daily range and mean)")))
    except Exception:  # noqa: BLE001
        pass

    # 3 Provenance
    story.append(Paragraph("3. Climate-data provenance", H2))
    rows = [["Field", "Value"]] + [[k, str(prov.get(k))[:180]] for k in (
        "provider", "product", "data_nature", "source_url", "retrieved_at", "requested_lat", "requested_lon", "actual_lat", "actual_lon",
        "distance_km", "elevation_difference_m", "spatial_resolution", "temporal_resolution", "timezone", "reference_period", "licence")]
    rows.append(["transformations", "; ".join(prov.get("transformations", []))[:400]])
    rows.append(["notes", "; ".join(prov.get("notes", []))[:400]])
    story.append(_table(rows, [40 * mm, 130 * mm]))

    # 4 Environmental constraints
    story.append(Paragraph("4. Environmental and operating constraints", H2))
    env = d.results[base_id].diagnostics.get("envelope_limits", {})
    cons = cfg["site"]["constraints"]
    story.append(_p(f"Server-inlet envelope (hard constraint): {env.get('name')} — {env.get('t_min_c')}–{env.get('t_max_c')} °C, dew point "
                    f"{env.get('dp_min_c')} to {env.get('dp_max_c')} °C, RH ≤ {env.get('rh_max_pct')} % ({env.get('source')}). "
                    f"Containment: {cfg['it']['containment']}; inlet setpoint {cfg['it']['inlet_setpoint_c']} °C. Water: source "
                    f"{cons['water_source']}, permitted withdrawal {cons['permitted_annual_withdrawal_m3']} m³/yr, stress score "
                    f"{cons['water_stress_score']} ({cons['water_stress_source'] or 'not provided'}). Dust {cons['dust_exposure']}, salinity "
                    f"{cons['salinity_exposure']}, flood {cons['flood_exposure']}."))

    # 5 Models
    story.append(Paragraph("5. Models, equations and assumptions", H2))
    for line in [
        "Psychrometrics: ASHRAE Fundamentals 2017 Ch. 1 (Hyland–Wexler saturation pressure; W = 0.621945 p_w/(p − p_w); thermodynamic wet bulb).",
        "Hall: ṁ_srv = Q_IT/(c_p ΔT_IT); ṁ_fac = ṁ_srv(1 + bypass); T_inlet = T_supply + ΔT_recirculation; T_return = T_supply + Q_zone/(ṁ c_p).",
        "Airflow network: ṁ = A √(2ρ|Δp|/K) sign(Δp); hydrostatics P_i − ρ_i g z; wind ½ρU_H²C_p (Swami–Chandra); Newton–Raphson on zone mass balances.",
        "EAHE: T_out = T_w + (T_in − T_w)e^(−NTU) per segment; radial implicit FD soil (disturbance form) with row-of-cylinders shape factor far field.",
        "Ground: T(z,t) = T_m − A_s e^(−z/δ) cos(ω(t − t₀) − z/δ), δ = √(2α/ω).",
        "Envelope: multilayer implicit FD conduction, sol-air exterior BC with sky long-wave exchange; transmission-matrix periodic response for checks.",
        "Chillers: COP = η_II T_evap/(T_cond − T_evap) calibrated to rated COP at AHRI rating; part-load PLF; N+1 staging and capacity derating.",
        "Evaporative/towers: explicit heat-and-mass balances; withdrawal = evaporation + blowdown + drift; consumption = evaporation + drift.",
        "Controller: hourly selection among feasible modes minimising J = w_E E/E₀ + w_C C/C₀ + w_W W/W₀ + w_K K/K₀, with hysteresis and minimum run time.",
        "PUE = ΣE_facility/ΣE_IT; WUE = Σwater/ΣE_IT; CO₂e = ΣE_facility,t I_t. Facility energy balance closed each hour within tolerance.",
    ]:
        story.append(_p("• " + line, SMALL))

    # 6 Baseline
    story.append(Paragraph("6. Baseline configuration", H2))
    sz = d.results[base_id].diagnostics.get("sizing", {})
    story.append(_p(f"{sz.get('architecture')}: design load {_f(sz.get('q_design_kw'))} kW, {sz.get('n_duty')} duty / {sz.get('n_installed')} "
                    f"installed units of {_f(sz.get('unit_capacity_kw'))} kW, rated COP {(sz.get('chiller') or {}).get('rated_cop')} "
                    f"({(sz.get('chiller') or {}).get('model')} model, η_II {_f(sz.get('eta_II'), 3)}), CHW supply {cfg['baseline']['chw_supply_c']} °C. "
                    f"N+1 failure check: {'passed' if d.results[base_id].diagnostics.get('failure', {}).get('n_minus_1', {}).get('passed') else 'FAILED'}."))

    # 7 Candidates
    story.append(Paragraph("7. Candidate strategies (biomimetic library)", H2))
    rows = [["ID", "Strategy", "Mechanism", "Auxiliary energy / water", "Confidence"]]
    for st in STRATEGIES:
        rows.append([st["id"], st["name"], st["physical_mechanism"][:160], f"{st['auxiliary_electricity']} / {st['water']}"[:120], st["confidence"][:80]])
    story.append(_table(rows, [8 * mm, 35 * mm, 62 * mm, 40 * mm, 27 * mm]))

    # 8 Comparative results
    story.append(PageBreak())
    story.append(Paragraph("8. Comparative simulation results", H2))
    rows = [["Scenario", "PUE", "Facility MWh", "Δ %", "Cooling MWh", "Peak kW", "Water m³", "WUE L/kWh", "CO₂e t", "Free-cooling h"]]
    for sid in sorted(comp):
        m, dp = comp[sid]["metrics"], comp[sid]["delta_pct"]
        rows.append([f"{sid} {comp[sid]['label'][:24]}", _f(m["pue"], 3), _f(m["facility_mwh"]), _f(dp.get("facility_mwh"), 1),
                     _f(m["cooling_mwh"]), _f(m["peak_facility_kw"]), _f(m["water_withdrawal_m3"]), _f(m["wue_l_per_kwh"], 3),
                     _f(m["co2e_t"]), _f(m["free_cooling_full_h"])])
    story.append(_table(rows))
    ids = sorted(comp)
    story.append(_img(charts.bar(ids, [comp[i]["metrics"]["pue"] for i in ids], "Annual PUE by scenario", "PUE (–)")))
    story.append(_img(charts.bar(ids, [comp[i]["metrics"]["water_withdrawal_m3"] for i in ids], "Annual site water withdrawal by scenario", "m³/yr")))
    hb, hd = d.hourly(base_id), d.hourly(best_id)
    months = sorted(set(pd.DatetimeIndex(hb["time_local"]).month))
    mb = hb["p_cooling_w"].groupby(pd.DatetimeIndex(hb["time_local"]).month).sum() / 1e6
    md = hd["p_cooling_w"].groupby(pd.DatetimeIndex(hd["time_local"]).month).sum() / 1e6
    story.append(_img(charts.monthly_compare(months, mb.to_numpy(), md.to_numpy(), "Monthly cooling electricity", "MWh",
                                             label_d=f"{best_id}")))
    story.append(_img(charts.mode_heatmap(hd, f"Operating mode by day and hour — {best_id}")))
    story.append(_img(charts.duration_curve({base_id: hb["q_coil_w"].to_numpy() / 1e3, best_id: hd["q_coil_w"].to_numpy() / 1e3},
                                            "Mechanical (coil) cooling load duration curve", "kW")))

    # 9 Recommendations
    story.append(Paragraph("9. Recommended architectural interventions", H2))
    for k in rec.get("ranking", [])[:5]:
        r = rec["candidates"][k]
        ex = r["explanation"]
        block = [_p(f"{k} — {ex['verdict']} (score {r['score']:.2f})", ParagraphStyle("x", parent=B, fontName="DejaVu-Bold")),
                 _p("Why: " + " ".join(ex.get("why_suitable", [])), SMALL),
                 _p("Climate evidence: " + " ".join(ex.get("climate_evidence", [])), SMALL),
                 _p("Mechanism: " + ex.get("mechanism", ""), SMALL),
                 _p(f"Annual benefit: {ex.get('annual_benefit')}. Peak: {ex.get('peak_benefit')}.", SMALL),
                 _p(f"Geometry: {ex.get('geometry')}. Auxiliary: {ex.get('auxiliary')}.", SMALL),
                 _p("Disadvantages: " + "; ".join(ex.get("disadvantages", [])), SMALL),
                 _p("Uncertainty: " + ex.get("uncertainty", ""), SMALL),
                 _p("Required before construction: " + "; ".join(ex.get("required_studies", [])), SMALL), Spacer(1, 4)]
        story.append(KeepTogether(block))
    infeasible = [(k, v) for k, v in rec.get("candidates", {}).items() if not v.get("feasible")]
    if infeasible:
        story.append(_p("Infeasible / excluded: " + "; ".join(f"{k}: {'; '.join(v['infeasible_reasons'])}" for k, v in infeasible), SMALL))

    # 10 Geometry & sizing / 11 balances / 12 carbon
    story.append(Paragraph("10. Energy and water balances", H2))
    rows = [["Scenario", "IT MWh", "Cooling MWh", "UPS loss MWh", "Distribution MWh", "Heat to plant MWh", "Exhaust MWh", "Ground MWh",
             "Closure"]]
    for sid in d.scenario_ids:
        sm = d.results[sid].summary
        c = sm["energy_mwh"]["components"]
        hp = sm["heat_mwh"]["paths"]
        rows.append([sid, _f(sm["energy_mwh"]["it"]), _f(sm["energy_mwh"]["cooling"]), _f(c.get("p_ups_loss")), _f(c.get("p_dist_loss")),
                     _f(hp["plant_condenser"]), _f(hp["economiser_exhaust"]), _f(hp["ground_eahe"]),
                     f"{100 * sm['heat_mwh']['closure']['max_relative_residual']:.2f} %"])
    story.append(_table(rows))
    story.append(_p(f"Water boundary: {base['water']['boundary']}. Withdrawal and consumption are reported separately (blowdown is "
                    "withdrawn but discharged).", SMALL))
    story.append(Paragraph("11. Carbon accounting", H2))
    story.append(_p(f"Factor: {base['carbon']['intensity_basis']}; source: {base['carbon']['source']}. Operational emissions only; "
                    "embodied carbon is reported where emission factors were supplied (lifecycle table)."))
    lc = comp.get(best_id, {}).get("lifecycle", {})
    if lc:
        emb = lc.get("embodied", {})
        story.append(_p(f"Lifecycle ({best_id}): capital cost {_f(lc.get('capex'))} {lc.get('currency')} "
                        f"(gaps: {', '.join(lc.get('capex_data_gaps', [])) or 'none'}); NPV {_f(lc.get('npv'))}; payback "
                        f"{_f(lc.get('simple_payback_years'), 1)} years; embodied carbon {_f(emb.get('total_kgco2e'))} kg CO₂e "
                        f"(data gaps: {', '.join(emb.get('data_gaps', [])) or 'none'}).", SMALL))

    # 13 Uncertainty
    story.append(Paragraph("12. Uncertainty and sensitivity", H2))
    tor = next((sres.result for sres in d.sensitivity if sres.kind == "tornado" and sres.result), None)
    if tor:
        story.append(_img(charts.tornado(tor["rows"], "One-at-a-time sensitivity of the saving vs baseline")))
    if mc and mc.get("by_category"):
        story.append(_p("Saving spread by uncertainty source (std, MWh): " + ", ".join(
            f"{k}: {v['saving_std_mwh']:.0f}" for k, v in mc["by_category"].items()), SMALL))
    if not tor and not mc:
        story.append(_p("No sensitivity analysis stored for this simulation.", SMALL))

    # 14 Failure modes / construction / data gaps / investigations
    story.append(Paragraph("13. Failure modes, construction and maintenance", H2))
    fl = d.results[best_id].diagnostics.get("failure", {})
    rt = fl.get("ride_through", {})
    story.append(_p(f"N+1 at the design hour ({fl.get('n_minus_1', {}).get('design_hour_t_db_c')} °C): "
                    f"{'passed' if fl.get('n_minus_1', {}).get('passed') else 'FAILED'}. Ride-through after total loss of cooling: "
                    f"{_f(rt.get('time_to_allowable_limit_s'))} s to {rt.get('limit_c')} °C ({rt.get('assumptions')})."))
    for st in STRATEGIES:
        if any(st["key"] in s for s in d.results[best_id].strategies):
            story.append(_p(f"{st['name']}: failure modes — {', '.join(st['failure_modes'])}; maintenance — {st['maintenance']}; "
                            f"constructability — {st['constructability']}", SMALL))
    story.append(Paragraph("14. Data gaps and further investigations", H2))
    defaults = [a for a in d.inputs.assumption_register if a["source"] == "default-assumption"]
    story.append(_p(f"{len(defaults)} of {len(d.inputs.assumption_register)} inputs use default assumptions (listed in the Excel export, "
                    "'Project Inputs'). Key items to confirm: IT load profile, equipment performance curves, soil properties, grid "
                    "emission factor, water limits and costs."))
    for sid in d.scenario_ids:
        for f in d.results[sid].flags:
            if f["level"] in ("warning", "error"):
                story.append(_p(f"{sid}: {f['message']}", SMALL))
    story.append(Paragraph("15. Limitations", H2))
    for x in LIMITATIONS:
        story.append(_p("• " + x, SMALL))
    story.append(Paragraph("16. References", H2))
    for x in REFERENCES:
        story.append(_p("• " + x, SMALL))

    doc.build(story)
    return buf.getvalue()
