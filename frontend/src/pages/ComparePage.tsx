import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useHourly } from "../api/hooks";
import { ChartCard } from "../charts/ChartCard";
import { barChart, lineChart, scatter, type Tokens } from "../charts/options";
import { useTokens } from "../charts/useTokens";
import { Callout, Empty, Field, Section, StatTile } from "../components/ui";
import { fmt, MONTHS, pct, SCENARIO_SHORT } from "../lib/format";
import { bestScenario, useProjectPage } from "../lib/project";
import type { ScenarioResult, ScenarioRow } from "../types/api";

const METRICS: { key: string; label: string; unit: string; digits: number; lowerBetter?: boolean }[] = [
  { key: "pue", label: "PUE (annual)", unit: "–", digits: 3 },
  { key: "facility_mwh", label: "Facility electricity", unit: "MWh/yr", digits: 0 },
  { key: "it_mwh", label: "IT electricity", unit: "MWh/yr", digits: 0 },
  { key: "cooling_mwh", label: "Cooling electricity", unit: "MWh/yr", digits: 0 },
  { key: "peak_facility_kw", label: "Peak facility power", unit: "kW", digits: 0 },
  { key: "peak_cooling_kw", label: "Peak cooling power", unit: "kW", digits: 0 },
  { key: "water_withdrawal_m3", label: "Water withdrawal", unit: "m³/yr", digits: 0 },
  { key: "water_consumption_m3", label: "Water consumption", unit: "m³/yr", digits: 0 },
  { key: "wue_l_per_kwh", label: "WUE (withdrawal)", unit: "L/kWh_IT", digits: 3 },
  { key: "co2e_t", label: "Operational CO₂e", unit: "t/yr", digits: 0 },
  { key: "heat_rejected_plant_mwh", label: "Heat rejected by plant condenser", unit: "MWh/yr", digits: 0 },
  { key: "free_cooling_full_h", label: "Full free-cooling hours", unit: "h/yr", digits: 0, lowerBetter: false },
  { key: "free_cooling_partial_h", label: "Partial free-cooling hours", unit: "h/yr", digits: 0, lowerBetter: false },
  { key: "unmet_hours", label: "Unmet-load hours", unit: "h/yr", digits: 0 },
  { key: "electricity_cost", label: "Electricity cost", unit: "currency/yr", digits: 0 },
  { key: "water_cost", label: "Water cost", unit: "currency/yr", digits: 0 },
];

const PATH_LABEL: Record<string, string> = {
  plant_condenser: "Mechanical plant (condenser)",
  economiser_exhaust: "Outdoor-air exhaust (economiser / stack)",
  exfiltration: "Exfiltration",
  ground_eahe: "Ground (earth-air exchanger)",
  iec_secondary_air: "Indirect-evaporative secondary air",
  radiative_sky: "Radiative sky panels",
  liquid_loop: "Liquid-cooling loop",
  free_economiser_waterside: "Waterside economiser",
};

const label = (sid: string) => `${sid} ${SCENARIO_SHORT[sid] ?? ""}`.trim();

function scenarioBars(t: Tokens, ids: string[], rows: Record<string, ScenarioRow>, key: string, unit: string, yName: string, opts: { yMin?: number } = {}) {
  return barChart(t, {
    categories: ids.map(label), yName, unit, labels: true, yMin: opts.yMin,
    series: [{ name: yName, data: ids.map((s) => rows[s].metrics[key] ?? null), colors: ids.map((s) => (s === "S0" ? t.neutral : t.series[0])) }],
  });
}

const annuity = (r: number, n: number) => Array.from({ length: n }, (_, i) => 1 / (1 + r) ** (i + 1)).reduce((a, b) => a + b, 0);

/** Sorted-descending duration curve resampled to ~200 points (x = % of hours). */
function duration(values: (number | string | boolean | null)[], scale: number): [number, number][] {
  const v = values.map(Number).filter(Number.isFinite).sort((a, b) => b - a);
  if (!v.length) return [];
  const n = v.length;
  const pts: [number, number][] = [];
  const stepN = Math.max(1, Math.floor(n / 200));
  for (let i = 0; i < n; i += stepN) pts.push([+(100 * i / (n - 1)).toFixed(2), v[i] * scale]);
  pts.push([100, v[n - 1] * scale]);
  return pts;
}

export function ComparePage() {
  const { results, simId, sim } = useProjectPage();
  const t = useTokens();
  const rows = results?.comparison;
  const ids = useMemo(() => (results ? results.scenarios.filter((s) => rows?.[s]) : []), [results, rows]);
  const best = bestScenario(rows);
  const [design, setDesign] = useState<string>("");
  useEffect(() => {
    if (best && !design) setDesign(best);
  }, [best, design]);
  const [monthlyMetric, setMonthlyMetric] = useState("facility_mwh");
  const hS0 = useHourly(simId, rows?.S0 ? "S0" : undefined, "q_coil_w,p_cooling_w");
  const hD = useHourly(simId, design && design !== "S0" ? design : undefined, "q_coil_w,p_cooling_w");

  if (sim.isLoading) return <p className="text-ink-2">Loading…</p>;
  if (!results || !rows) {
    return (
      <Empty title="No completed simulation">
        Comparative charts appear after a scenario run finishes. Run the scenarios from the Setup page.
        {sim.data?.status === "failed" && <><br />The last run failed: {sim.data.error}</>}
      </Empty>
    );
  }
  const sr: Record<string, ScenarioResult> = Object.fromEntries((results.scenario_results ?? []).map((r) => [r.scenario_id, r]));
  const d = sr[design];
  const s0 = sr.S0;
  const period = `${results.climate_summary?.period.start_utc.slice(0, 10)} – ${results.climate_summary?.period.end_utc.slice(0, 10)}`;
  const src = `Second Nature simulation ${simId?.slice(0, 8)} (model ${results.model_version}), identical climate, IT load and redundancy for all scenarios`;
  const currency = rows.S0?.lifecycle?.currency ?? "";
  const tableRows = (key: string, digits = 3) => ids.map((s) => [s, rows[s].label, rows[s].metrics[key], rows[s].delta_pct[key] ?? null].map((v) => (typeof v === "number" ? +v.toFixed(digits) : v)));
  const tbl = (key: string, unit: string) => ({ columns: ["scenario", "label", `${key} (${unit})`, "delta_vs_S0_pct"], rows: tableRows(key) });
  const noCarbon = ids.every((s) => rows[s].metrics.co2e_t === null || rows[s].metrics.co2e_t === undefined);

  // heat-rejection paths present in any scenario (unmet is not a rejection path)
  const paths = Object.keys(PATH_LABEL).filter((p) => ids.some((s) => Math.abs(sr[s]?.summary.heat_mwh.paths[p] ?? 0) > 0.5));

  // lifecycle (only scenarios whose capital cost is complete)
  const lc = ids.map((s) => ({ s, l: rows[s].lifecycle })).filter((x) => x.l);
  const lcComplete = lc.filter((x) => x.l!.capex !== null && x.l!.annual_operating_cost !== null);
  const capexGaps = Array.from(new Set(lc.flatMap((x) => x.l!.capex_data_gaps ?? [])));
  // only designs that add strategies have an incremental capital cost to plot against
  const withStrategies = (sid: string) => sid !== "S0" && rows[sid].strategies.length > 0;
  const lcUseful = lcComplete.some((x) => withStrategies(x.s));
  const savingVsCapex = lcComplete.filter((x) => withStrategies(x.s)).map((x) => [x.l!.capex, (rows.S0.metrics.cooling_mwh ?? 0) - (rows[x.s].metrics.cooling_mwh ?? 0), x.s] as [number, number, string]);
  const carbonRows = lc.filter((x) => x.l!.embodied?.complete && x.l!.operational_carbon_lifetime_t !== null);
  const carbonUseful = carbonRows.some((x) => (x.l!.embodied?.items?.length ?? 0) > 0);
  const embodiedGaps = Array.from(new Set(lc.flatMap((x) => (x.l!.embodied?.data_gaps ?? []) as string[])));

  const monthly = (sid: string) => sr[sid]?.summary.monthly as Record<string, number[]> | undefined;
  const mMeta = { facility_mwh: ["Facility electricity", "MWh"], cooling_mwh: ["Cooling electricity", "MWh"], pue: ["PUE", "–"], water_m3: ["Water withdrawal", "m³"], residual_mech_cooling_mwh: ["Residual mechanical cooling", "MWh_th"] } as Record<string, [string, string]>;
  const dur0 = hS0.data ? duration(hS0.data.columns.q_coil_w, 1e-3) : [];
  const durD = hD.data ? duration(hD.data.columns.q_coil_w, 1e-3) : design === "S0" ? dur0 : [];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[20px] font-semibold">Comparative performance</h1>
          <p className="text-[12px] text-ink-2 max-w-4xl">
            Every scenario uses the same climate record ({period}), IT load profile, environmental class, redundancy and accounting boundary.
            Baseline S0 is shown in grey. Percentages are relative to S0. Facility electricity includes IT, cooling, UPS/distribution losses, lighting and controls.
          </p>
        </div>
        <div className="flex gap-2 items-end">
          <Field label="Design to compare with S0">
            <select className="input" value={design} onChange={(e) => setDesign(e.target.value)}>
              {ids.map((s) => <option key={s} value={s}>{label(s)} — {rows[s].label}{s === best ? " (best objective)" : ""}</option>)}
            </select>
          </Field>
          <a className="btn" href={api.url(`/api/simulations/${simId}/comparison.csv`)}>Comparison CSV</a>
        </div>
      </div>

      {d && rows[design] && (
        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-3">
          <StatTile label={`PUE · ${design}`} value={fmt(rows[design].metrics.pue, 3)} delta={rows[design].delta_pct.pue} />
          <StatTile label="Facility electricity" value={fmt(rows[design].metrics.facility_mwh)} unit="MWh/yr" delta={rows[design].delta_pct.facility_mwh} />
          <StatTile label="Cooling electricity" value={fmt(rows[design].metrics.cooling_mwh)} unit="MWh/yr" delta={rows[design].delta_pct.cooling_mwh} />
          <StatTile label="Water withdrawal" value={fmt(rows[design].metrics.water_withdrawal_m3)} unit="m³/yr" delta={rows[design].delta_pct.water_withdrawal_m3} />
          <StatTile label="Operational CO₂e" value={rows[design].metrics.co2e_t == null ? "n/a" : fmt(rows[design].metrics.co2e_t)} unit={rows[design].metrics.co2e_t == null ? undefined : "t/yr"}
            delta={rows[design].delta_pct.co2e_t} sub={rows[design].metrics.co2e_t == null ? "No grid emission factor supplied" : undefined} />
          <StatTile label="Unmet-load hours" value={fmt(rows[design].metrics.unmet_hours)} unit="h" sub="Hard constraint: must be 0 for a valid design" />
        </div>
      )}
      {rows[design]?.flags.filter((f) => f.level !== "info").map((f) => <Callout key={f.code} level={f.level}>{design}: {f.message}</Callout>)}

      <Section title="Absolute values and change versus baseline">
        <div className="card overflow-auto">
          <table className="w-full text-[12px] tnum">
            <thead>
              <tr className="border-b border-line">
                <th className="text-left px-2 py-1.5 font-semibold sticky left-0 bg-surface">Metric</th>
                {ids.map((s) => <th key={s} className="text-right px-2 py-1.5 font-semibold" title={rows[s].description}>{label(s)}</th>)}
              </tr>
            </thead>
            <tbody>
              {METRICS.map((m) => (
                <tr key={m.key} className="border-b border-line">
                  <td className="px-2 py-1 sticky left-0 bg-surface">{m.label} <span className="text-ink-3">({m.unit === "currency/yr" ? `${currency}/yr` : m.unit})</span></td>
                  {ids.map((s) => {
                    const v = rows[s].metrics[m.key];
                    const dp = rows[s].delta_pct[m.key];
                    return (
                      <td key={s} className="text-right px-2 py-1 whitespace-nowrap">
                        {v === null || v === undefined ? <span className="text-ink-3">n/a</span> : fmt(v, m.digits)}
                        {s !== "S0" && dp !== null && dp !== undefined && <span className="text-ink-3 ml-1 text-[11px]">{pct(dp)}</span>}
                      </td>
                    );
                  })}
                </tr>
              ))}
              <tr className="border-b border-line">
                <td className="px-2 py-1 sticky left-0 bg-surface">Incremental capital cost <span className="text-ink-3">({currency})</span></td>
                {ids.map((s) => {
                  const l = rows[s].lifecycle;
                  return <td key={s} className="text-right px-2 py-1">{l?.capex == null ? <span className="text-ink-3" title={l?.capex_data_gaps?.join(", ")}>data gap</span> : fmt(l.capex)}</td>;
                })}
              </tr>
              <tr className="border-b border-line">
                <td className="px-2 py-1 sticky left-0 bg-surface">NPV of savings vs S0 <span className="text-ink-3">({currency})</span></td>
                {ids.map((s) => {
                  const l = rows[s].lifecycle;
                  return <td key={s} className="text-right px-2 py-1">{s === "S0" ? "–" : l?.npv == null ? <span className="text-ink-3">{l?.note ? "data gap" : "n/a"}</span> : fmt(l.npv)}</td>;
                })}
              </tr>
              <tr>
                <td className="px-2 py-1 sticky left-0 bg-surface">Embodied carbon of strategies <span className="text-ink-3">(t CO₂e)</span></td>
                {ids.map((s) => {
                  const e = rows[s].lifecycle?.embodied;
                  return <td key={s} className="text-right px-2 py-1">{e?.total_kgco2e == null || !e.complete ? <span className="text-ink-3">{e?.items?.length ? "data gap" : "–"}</span> : fmt(e.total_kgco2e / 1000, 1)}</td>;
                })}
              </tr>
            </tbody>
          </table>
        </div>
        <p className="text-[11px] text-ink-3">“data gap”: capital costs and embodied-carbon factors are only used when supplied on the Setup page (economics); none are invented.</p>
      </Section>

      <Section title="Annual metrics by scenario">
        <div className="grid lg:grid-cols-2 xl:grid-cols-3 gap-4">
          <ChartCard id="c21-pue" number={21} title="Annual PUE by scenario" units="facility energy ÷ IT energy (axis starts at 1.0 = zero overhead)" period={period} source={src}
            assumptions="Annual-energy-weighted PUE (not the mean of hourly values)." option={scenarioBars(t, ids, rows, "pue", "", "PUE", { yMin: 1 })} table={tbl("pue", "-")} />
          <ChartCard id="c22-facility" number={22} title="Annual facility electricity by scenario" units="MWh/yr" period={period} source={src}
            option={scenarioBars(t, ids, rows, "facility_mwh", "MWh", "MWh/yr")} table={tbl("facility_mwh", "MWh")} />
          <ChartCard id="c23-cooling" number={23} title="Cooling electricity by scenario" units="MWh/yr" period={period} source={src}
            assumptions="Chillers, towers/dry coolers, pumps, CRAH/AHU and EAHE fans, humidifiers and actuators."
            option={scenarioBars(t, ids, rows, "cooling_mwh", "MWh", "MWh/yr")} table={tbl("cooling_mwh", "MWh")} />
          <ChartCard id="c24-peak" number={24} title="Peak power demand by scenario" units="kW" period={period} source={src}
            assumptions="Highest hourly value in the record; the mechanical plant is still sized for the design hour."
            option={barChart(t, { categories: ids.map(label), yName: "kW", unit: "kW", series: [
              { name: "Peak facility", data: ids.map((s) => rows[s].metrics.peak_facility_kw ?? null), color: t.series[0] },
              { name: "Peak cooling", data: ids.map((s) => rows[s].metrics.peak_cooling_kw ?? null), color: t.series[1] },
            ] })}
            table={{ columns: ["scenario", "peak_facility_kw", "peak_cooling_kw"], rows: ids.map((s) => [s, rows[s].metrics.peak_facility_kw, rows[s].metrics.peak_cooling_kw]) }} />
          <ChartCard id="c25-water" number={25} title="Annual water use by scenario" units="m³/yr" period={period} source={src}
            assumptions="Site operational water only (towers, evaporative media, humidification, pre-cooling). Excludes water embedded in electricity generation."
            option={barChart(t, { categories: ids.map(label), yName: "m³/yr", unit: "m³", series: [
              { name: "Withdrawal", data: ids.map((s) => rows[s].metrics.water_withdrawal_m3 ?? null), color: t.series[0] },
              { name: "Consumption (evaporated)", data: ids.map((s) => rows[s].metrics.water_consumption_m3 ?? null), color: t.series[1] },
            ] })}
            table={{ columns: ["scenario", "withdrawal_m3", "consumption_m3"], rows: ids.map((s) => [s, rows[s].metrics.water_withdrawal_m3, rows[s].metrics.water_consumption_m3]) }} />
          <ChartCard id="c26-wue" number={26} title="WUE by scenario" units="L per kWh of IT energy (withdrawal basis)" period={period} source={src}
            option={scenarioBars(t, ids, rows, "wue_l_per_kwh", "L/kWh", "L/kWh")} table={tbl("wue_l_per_kwh", "L/kWh")} />
          <ChartCard id="c27-co2" number={27} title="Annual operational CO₂e by scenario" units="t CO₂e/yr" period={period} source={`${src}; grid factor: ${s0?.summary.carbon.source ?? "n/a"} (${s0?.summary.carbon.intensity_basis ?? ""})`}
            assumptions={`Location-based, ${s0?.summary.carbon.factor_type ?? ""} emission factor applied to facility electricity.`}
            empty={noCarbon ? "No grid emission factor was supplied, so operational carbon is not calculated. Add an annual/monthly factor or upload an hourly series on the Setup page." : undefined}
            option={noCarbon ? undefined : scenarioBars(t, ids, rows, "co2e_t", "t", "t CO₂e/yr")} table={tbl("co2e_t", "t")} />
          <ChartCard id="c28-heat" number={28} title="Cooling-system heat rejection by path" units="MWh_th/yr" period={period} source={`${src}; facility energy balance`}
            assumptions="Where the heat leaves the facility. Energy-balance closure is checked hourly for every scenario."
            empty={!paths.length ? "No heat-rejection data." : undefined}
            option={paths.length ? barChart(t, { categories: ids.map(label), yName: "MWh/yr", unit: "MWh", legend: true,
              series: paths.slice(0, 8).map((p, i) => ({ name: PATH_LABEL[p], stack: "heat", color: t.series[i], data: ids.map((s) => sr[s]?.summary.heat_mwh.paths[p] ?? null) })) }) : undefined}
            table={{ columns: ["scenario", ...paths], rows: ids.map((s) => [s, ...paths.map((p) => sr[s]?.summary.heat_mwh.paths[p] ?? null)]) }} className="xl:col-span-2" />
          <ChartCard id="c29-savings-capex" number={29} title="Cooling-energy savings versus capital cost" units={`MWh/yr saved vs S0 · ${currency} incremental capital`} period={period} source={`${src}; economics inputs`}
            assumptions="Capital costs are user-supplied quotes/estimates; nothing is estimated by the platform."
            empty={savingVsCapex.length ? undefined : `Capital cost not supplied${capexGaps.length ? ` for: ${capexGaps.join(", ")}` : ""}. Enter costs under Setup → Economics to populate this chart.`}
            option={savingVsCapex.length ? scatter(t, { series: [{ name: "Scenario", data: savingVsCapex }], xName: `Capital cost (${currency})`, yName: "MWh/yr", xUnit: currency, yUnit: "MWh/yr", tooltipExtra: (v) => `<div>${String(v[2])}</div>` }) : undefined}
            table={{ columns: ["capex", "cooling_saving_mwh", "scenario"], rows: savingVsCapex }} />
        </div>
      </Section>

      <Section title="Lifecycle">
        <div className="grid lg:grid-cols-2 gap-4">
          <ChartCard id="c30-lcc" number={30} title="Lifecycle cost comparison" units={`${currency}, present value over ${rows.S0?.lifecycle?.horizon_years ?? "?"} years at ${((rows.S0?.lifecycle?.discount_rate ?? 0) * 100).toFixed(1)} %`} period="design life" source={`${src}; economics inputs`}
            assumptions="Incremental capital of passive/hybrid strategies + PV of electricity, water and maintenance. Base plant capital common to all scenarios is excluded."
            empty={lcUseful ? undefined : `Requires electricity price and capital costs${capexGaps.length ? ` (missing for: ${capexGaps.join(", ")})` : ""}.`}
            option={lcUseful ? barChart(t, { categories: lcComplete.map((x) => label(x.s)), yName: currency, unit: currency, legend: true, series: [
              { name: "Incremental capital", stack: "lcc", color: t.series[1], data: lcComplete.map((x) => x.l!.capex) },
              { name: "PV operating + maintenance", stack: "lcc", color: t.series[0], data: lcComplete.map((x) => (x.l!.annual_operating_cost + (x.l!.annual_maintenance ?? 0)) * annuity(x.l!.discount_rate, x.l!.horizon_years)) },
            ] }) : undefined}
            table={{ columns: ["scenario", "capex", "annual_operating_cost", "annual_maintenance", "npv_vs_S0", "simple_payback_years"], rows: lc.map((x) => [x.s, x.l!.capex, x.l!.annual_operating_cost, x.l!.annual_maintenance, x.l!.npv ?? null, x.l!.simple_payback_years ?? null]) }} />
          <ChartCard id="c31-carbon" number={31} title="Embodied versus operational carbon" units={`t CO₂e over ${rows.S0?.lifecycle?.horizon_years ?? "?"} years`} period="design life" source={`${src}; embodied factors supplied by user`}
            assumptions="Embodied: material quantities from strategy geometry × user-supplied factors (strategies only, not the base building). Operational: annual × horizon, constant grid factor."
            empty={carbonUseful ? undefined : noCarbon ? "No grid emission factor supplied." : `Embodied-carbon factors missing${embodiedGaps.length ? ` for: ${embodiedGaps.join(", ")}` : ""}.`}
            option={carbonUseful ? barChart(t, { categories: carbonRows.map((x) => label(x.s)), yName: "t CO₂e", unit: "t", legend: true, series: [
              { name: "Embodied (strategies)", stack: "c", color: t.series[1], data: carbonRows.map((x) => (x.l!.embodied.total_kgco2e ?? 0) / 1000) },
              { name: "Operational (lifetime)", stack: "c", color: t.series[0], data: carbonRows.map((x) => x.l!.operational_carbon_lifetime_t) },
            ] }) : undefined}
            table={{ columns: ["scenario", "embodied_t", "operational_lifetime_t", "complete"], rows: lc.map((x) => [x.s, x.l!.embodied?.total_kgco2e == null ? null : x.l!.embodied.total_kgco2e / 1000, x.l!.operational_carbon_lifetime_t, x.l!.embodied?.complete]) }} />
        </div>
      </Section>

      <Section title={`Baseline (S0) versus ${design ? label(design) : "design"}`}>
        {!d || !s0 ? <Callout>Select a design scenario.</Callout> : (
          <div className="grid lg:grid-cols-2 gap-4">
            <ChartCard id="c35-monthly" number={35} title={`Monthly ${mMeta[monthlyMetric][0].toLowerCase()}: baseline versus ${design}`} units={mMeta[monthlyMetric][1]} period={period} source={src} className="lg:col-span-2"
              option={lineChart(t, { x: (monthly("S0")!.month ?? []).map((m) => MONTHS[m - 1]), yName: mMeta[monthlyMetric][1], unit: mMeta[monthlyMetric][1], yMin: monthlyMetric === "pue" ? 1 : 0, series: [
                { name: "S0 baseline", data: monthly("S0")![monthlyMetric], color: t.neutral },
                ...(design !== "S0" ? [{ name: `${design} ${rows[design].label}`, data: monthly(design)![monthlyMetric], color: t.series[0], area: true }] : []),
              ] })}
              table={{ columns: ["month", `S0_${monthlyMetric}`, `${design}_${monthlyMetric}`], rows: (monthly("S0")!.month ?? []).map((m, i) => [m, monthly("S0")![monthlyMetric][i], monthly(design)![monthlyMetric][i]]) }}>
            </ChartCard>
            <div className="lg:col-span-2 -mt-2 flex gap-2 flex-wrap text-[12px]" role="group" aria-label="Monthly metric">
              {Object.entries(mMeta).map(([k, [lab]]) => (
                <button key={k} className={`btn !py-1 !px-2 ${k === monthlyMetric ? "btn-primary" : ""}`} aria-pressed={k === monthlyMetric} onClick={() => setMonthlyMetric(k)}>{lab}</button>
              ))}
            </div>
            <ChartCard id="c12-paths" number={12} title={`Cooling delivered by strategy — ${design}`} units="MWh_th/yr of heat removed" period={period} source={`${src}; facility energy balance`}
              assumptions="Heat leaving through each path. Outdoor-air exhaust covers economiser, stack and earth-air-precooled intake modes."
              option={barChart(t, { categories: paths.map((p) => PATH_LABEL[p]), yName: "MWh/yr", unit: "MWh", horizontal: true, labels: true, series: [
                { name: design, data: paths.map((p) => d.summary.heat_mwh.paths[p] ?? 0), color: t.series[0] },
              ] })}
              table={{ columns: ["path", `${design}_mwh`, "S0_mwh"], rows: paths.map((p) => [p, d.summary.heat_mwh.paths[p], s0.summary.heat_mwh.paths[p]]) }} />
            <ChartCard id="c18-envelope" number={18} title="Envelope heat gain by month" units="MWh_th (negative = net loss to outdoors)" period={period} source={`${src}; FD envelope model`}
              assumptions="Conduction + solar through roof, walls and slab, including shading and roof reflectance of each scenario."
              option={barChart(t, { categories: (monthly("S0")!.month ?? []).map((m) => MONTHS[m - 1]), yName: "MWh", unit: "MWh", legend: true, series: [
                { name: "S0 baseline", data: monthly("S0")!.envelope_mwh, color: t.neutral },
                ...(design !== "S0" ? [{ name: design, data: monthly(design)!.envelope_mwh, color: t.series[0] }] : []),
              ] })}
              table={{ columns: ["month", "S0_envelope_mwh", `${design}_envelope_mwh`], rows: (monthly("S0")!.month ?? []).map((m, i) => [m, monthly("S0")!.envelope_mwh[i], monthly(design)!.envelope_mwh[i]]) }} />
            <ChartCard id="c19-ldc" number={19} title="Cooling load duration curve (mechanical plant)" units="kW_th of coil load met by the chiller plant" period={period} source={`${src}; hourly ledger`}
              assumptions="Hourly residual load on the mechanical plant, sorted from highest to lowest. Passive and economiser hours appear as zero load."
              empty={!dur0.length ? (hS0.isLoading ? "Loading hourly results…" : "No hourly data.") : undefined}
              option={dur0.length ? lineChart(t, { xValue: { name: "% of hours in the year", unit: "%", min: 0, max: 100 }, yName: "kW", unit: "kW", yMin: 0, series: [
                { name: "S0 baseline", data: dur0, color: t.neutral },
                ...(design !== "S0" && durD.length ? [{ name: design, data: durD, color: t.series[0] }] : []),
              ] }) : undefined}
              table={{ columns: ["pct_of_hours", "S0_kw", `${design}_kw`], rows: dur0.map((p, i) => [p[0], p[1], durD[i]?.[1] ?? null]) }}
            />
            <div className="card p-4 text-[12px] flex flex-col gap-1">
              <div className="font-semibold text-[13px]">Mode hours — {design}</div>
              {Object.entries(d.summary.modes.hours as Record<string, number>).filter(([, h]) => h > 0).map(([m, h]) => (
                <div key={m} className="flex justify-between tnum"><span className="text-ink-2">{m}</span><span>{fmt(h)} h</span></div>
              ))}
              <div className="flex justify-between tnum border-t border-line pt-1 mt-1"><span className="text-ink-2">Mode switches</span><span>{fmt(d.summary.modes.switches)}</span></div>
              <div className="flex justify-between tnum"><span className="text-ink-2">Energy-balance closure</span><span>{d.summary.heat_mwh.closure.passed ? "✓ passed" : "⚠ check"} · max hourly residual {fmt(d.summary.heat_mwh.closure.max_relative_residual * 100, 2)} %</span></div>
            </div>
          </div>
        )}
      </Section>
    </div>
  );
}
