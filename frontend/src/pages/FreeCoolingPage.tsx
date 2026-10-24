import { useMemo, useState } from "react";
import { useHourly } from "../api/hooks";
import { ChartCard } from "../charts/ChartCard";
import { barChart, heatmap, lineChart } from "../charts/options";
import { useTokens } from "../charts/useTokens";
import { Callout, Empty, Section, StatTile } from "../components/ui";
import { fmt, MODE_LABELS, MODES, MONTHS, SCENARIO_SHORT } from "../lib/format";
import { bestScenario, useProjectPage } from "../lib/project";

export function FreeCoolingPage() {
  const { results, simId, sim } = useProjectPage();
  const t = useTokens();
  const ids = results?.scenario_results?.map((r) => r.scenario_id) ?? [];
  const best = bestScenario(results?.comparison);
  const [scenario, setScenario] = useState<string | undefined>();
  const sid = scenario ?? best ?? ids[0];
  const hourly = useHourly(simId, sid, "time_local,mode,partial,t_oa_c,t_dp_oa_c,rh_oa_pct,t_supply_c,t_inlet_c,q_coil_w,p_cooling_w,unmet_w");
  const base = useHourly(simId, "S0", "time_local,q_coil_w");
  const res = results?.scenario_results?.find((r) => r.scenario_id === sid);
  const s0 = results?.scenario_results?.find((r) => r.scenario_id === "S0");

  const derived = useMemo(() => {
    const h = hourly.data;
    if (!h || !res) return null;
    const env = res.diagnostics.envelope_limits;
    const tmax = res.diagnostics.t_econ_max_c as number;
    const local = (h.columns.time_local as string[]).map((x) => new Date(x.replace(" ", "T")));
    const month = local.map((d) => d.getMonth());
    const mode = h.columns.mode as string[];
    const partial = h.columns.partial as boolean[];
    const toa = h.columns.t_oa_c as number[];
    const tdp = h.columns.t_dp_oa_c as number[];
    const dry = Array(12).fill(0), full = Array(12).fill(0), actual = Array(12).fill(0), part = Array(12).fill(0), humid = Array(12).fill(0);
    for (let i = 0; i < mode.length; i++) {
      const m = month[i];
      const dbOk = toa[i] <= tmax;
      const hHigh = tdp[i] > env.dp_max_c;
      const hLow = tdp[i] < env.dp_min_c;
      if (dbOk) dry[m]++;
      if (dbOk && !hHigh && !hLow) full[m]++;
      if (dbOk && hHigh) humid[m]++;
      const free = mode[i] !== "mechanical" && mode[i] !== "mech_precool";
      if (free && !partial[i]) actual[m]++;
      if (free && partial[i]) part[m]++;
    }
    // calendar: day of year × local hour → mode index (+0.5 for partial)
    const start = new Date(local[0].getFullYear(), 0, 1).getTime();
    const cells: [number, number, number][] = [];
    for (let i = 0; i < mode.length; i++) {
      const doy = Math.floor((local[i].getTime() - start) / 86400000);
      if (doy < 0 || doy > 365) continue;
      cells.push([doy, local[i].getHours(), MODES.indexOf(mode[i]) * 2 + (partial[i] && mode[i] !== "mechanical" ? 1 : 0)]);
    }
    return { dry, full, actual, part, humid, cells, local, tmax, env };
  }, [hourly.data, res]);

  if (sim.isLoading) return <p className="text-ink-2">Loading…</p>;
  if (!results) return <Empty title="No simulation yet">Run the scenarios on the Setup page; free-cooling results come from the hourly controller, not from weather alone.</Empty>;

  const fc = res?.summary.free_cooling;
  const period = `${results.climate_summary?.period.start_utc.slice(0, 10)} – ${results.climate_summary?.period.end_utc.slice(0, 10)}`;
  const src = `Second Nature hourly controller, simulation ${simId?.slice(0, 8)}, model ${results.model_version}`;
  const usedModes = MODES.filter((m) => (res?.summary.modes.hours[m] ?? 0) > 0);
  const modeColor = (m: string) => (m === "mechanical" ? t.neutral : t.series[MODES.indexOf(m) - 1] ?? t.series[0]);
  const pieces = usedModes.flatMap((m) => {
    const i = MODES.indexOf(m);
    const p = [{ value: i * 2, label: MODE_LABELS[m], color: modeColor(m) }];
    if (m !== "mechanical" && m !== "mech_precool") p.push({ value: i * 2 + 1, label: `${MODE_LABELS[m]} + trim`, color: shade(modeColor(m)) });
    return p;
  });
  const resMonthly = (r: typeof res) => (r?.summary.monthly.residual_mech_cooling_mwh as number[] | undefined) ?? [];
  const toa = hourly.data?.columns.t_oa_c as number[] | undefined;
  const tsup = hourly.data?.columns.t_supply_c as number[] | undefined;
  const times = derived?.local.map((d) => d.getTime()) ?? [];

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap justify-between items-end gap-3">
        <div>
          <h1 className="text-[20px] font-semibold">Free-cooling analysis</h1>
          <p className="text-[12px] text-ink-2">Environmental eligibility of outdoor air is shown separately from useful cooling actually delivered by the controller.</p>
        </div>
        <label className="text-[12px] text-ink-2 flex items-center gap-1">Scenario
          <select className="input !w-auto" value={sid} onChange={(e) => setScenario(e.target.value)}>
            {ids.map((i) => <option key={i} value={i}>{i} · {results.comparison?.[i]?.label}</option>)}
          </select>
        </label>
      </div>
      {fc && (
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
          <StatTile label="Dry-bulb eligible hours" value={fmt(fc.eligibility.dry_bulb_eligible_h)} unit="h" sub={`T_oa ≤ ${fmt(derived?.tmax, 1)} °C supply limit`} />
          <StatTile label="Enthalpy-eligible hours" value={fmt(fc.eligibility.enthalpy_eligible_h)} unit="h" sub="outdoor enthalpy below return" />
          <StatTile label="Full-envelope eligible" value={fmt(fc.eligibility.full_envelope_eligible_h)} unit="h" sub={`${fmt(fc.eligibility.full_envelope_eligible_pct, 0)} % of hours`} />
          <StatTile label="Full free cooling delivered" value={fmt(fc.full_hours)} unit="h" sub={`${fmt(fc.full_pct, 0)} % of hours`} />
          <StatTile label="Partial (with mechanical trim)" value={fmt(fc.partial_hours)} unit="h" />
          <StatTile label="Residual mechanical cooling" value={fmt(fc.residual_mechanical_cooling_mwh)} unit="MWh" sub={`baseline ${fmt(s0?.summary.free_cooling.residual_mechanical_cooling_mwh)} MWh`} />
        </div>
      )}
      {fc && <Callout level="info" title="Rejections">humidity too high {fmt(fc.eligibility.rejected_humidity_high_h)} h · humidity too low {fmt(fc.eligibility.rejected_humidity_low_h)} h · temperature {fmt(fc.eligibility.rejected_temperature_h)} h · air quality {fmt(fc.eligibility.rejected_air_quality_h)} h. Dry-bulb eligibility alone overstates free cooling.</Callout>}

      <div className="grid xl:grid-cols-2 gap-4">
        <ChartCard id="c10-eligibility" number={10} title="Free-cooling eligibility and delivery by month" units="hours per month" period={`${period}, local months`} source={src}
          assumptions="Eligibility: outdoor dry bulb within the economiser supply limit, and dew point within the inlet envelope. Delivered: hours the controller ran a free-cooling mode without mechanical trim."
          empty={!derived ? "Loading hourly results…" : undefined}
          option={derived ? barChart(t, { categories: MONTHS, yName: "h", unit: "h", series: [
            { name: "Dry-bulb eligible", data: derived.dry, color: t.series[0] },
            { name: "Full-envelope eligible", data: derived.full, color: t.series[2] },
            { name: "Full free cooling delivered", data: derived.actual, color: t.series[1] },
          ] }) : undefined}
          table={derived ? { columns: ["month", "dry_bulb_eligible_h", "full_envelope_eligible_h", "humidity_excluded_h", "delivered_full_h", "delivered_partial_h"],
            rows: MONTHS.map((m, i) => [m, derived.dry[i], derived.full[i], derived.humid[i], derived.actual[i], derived.part[i]]) } : undefined} />
        <ChartCard id="c11-mode-hours" number={11} title="Annual cooling-mode operating hours" units="hours per year" period={period} source={src}
          option={res ? barChart(t, { categories: usedModes.map((m) => MODE_LABELS[m]), horizontal: true, labels: true, yName: "h", unit: "h",
            series: [{ name: "hours", data: usedModes.map((m) => res.summary.modes.hours[m]), colors: usedModes.map(modeColor) }] }) : undefined}
          table={res ? { columns: ["mode", "hours", "cooling_mwh"], rows: MODES.map((m) => [m, res.summary.modes.hours[m], res.summary.energy_mwh.by_mode_cooling[m]]) } : undefined}
          height={Math.max(180, usedModes.length * 34)} />
      </div>
      <ChartCard id="c-calendar" title={`Operating mode by day and hour — ${sid} ${SCENARIO_SHORT[sid ?? ""] ?? ""}`} units="mode (lighter shade = free cooling with mechanical trim)" period={`${period}, local time`} source={src}
        empty={!derived ? "Loading hourly results…" : undefined}
        option={derived ? heatmap(t, { x: Array.from({ length: 366 }, (_, i) => i + 1), y: Array.from({ length: 24 }, (_, i) => i), xName: "day of year", yName: "hour", unit: "", data: derived.cells, pieces }) : undefined}
        table={hourly.data ? { columns: ["time_local", "mode", "partial"], rows: (hourly.data.columns.time_local as string[]).map((tl, i) => [tl, (hourly.data!.columns.mode as string[])[i], (hourly.data!.columns.partial as boolean[])[i]]) } : undefined}
        height={340} />
      <div className="grid xl:grid-cols-2 gap-4">
        <ChartCard id="c-timeseries" title="Outdoor and supply-air temperature" units="°C" period={period} source={src}
          assumptions="Supply temperature floats up to the economiser limit in free-cooling hours and sits at the setpoint otherwise."
          empty={!derived ? "Loading…" : undefined}
          option={toa && tsup ? lineChart(t, { time: true, zoom: true, yName: "°C", unit: "°C", series: [
            { name: "Outdoor dry bulb", data: times.map((x, i) => [x, toa[i]] as [number, number]), color: t.series[0] },
            { name: "Supply air", data: times.map((x, i) => [x, tsup[i]] as [number, number]), color: t.series[1] },
          ] }) : undefined}
          table={toa && tsup ? { columns: ["time_local", "t_oa_c", "t_supply_c"], rows: (hourly.data!.columns.time_local as string[]).map((tl, i) => [tl, toa[i], tsup[i]]) } : undefined} />
        <ChartCard id="c20-residual" number={20} title="Residual mechanical cooling demand" units="MWh thermal per month (coil load)" period={period} source={src}
          option={res && s0 ? barChart(t, { categories: MONTHS, yName: "MWh", unit: "MWh", series: [
            { name: "S0 baseline", data: resMonthly(s0), color: t.neutral },
            { name: `${sid} ${res.label}`, data: resMonthly(res), color: t.series[0] },
          ] }) : undefined}
          table={res && s0 ? { columns: ["month", "S0_mwh", `${sid}_mwh`], rows: MONTHS.map((m, i) => [m, resMonthly(s0)[i], resMonthly(res)[i]]) } : undefined} />
      </div>
      {res && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatTile label="Peak mechanical coil load" value={fmt(res.summary.power_kw.peak_plant_coil_load)} unit="kW" sub={`baseline ${fmt(s0?.summary.power_kw.peak_plant_coil_load)} kW`} />
          <StatTile label="Peak cooling electricity" value={fmt(res.summary.power_kw.peak_cooling)} unit="kW" sub={`baseline ${fmt(s0?.summary.power_kw.peak_cooling)} kW`} />
          <StatTile label="Max server inlet" value={fmt(res.summary.thermal.max_inlet_c, 1)} unit="°C" sub={`envelope ≤ ${res.diagnostics.envelope_limits.t_max_c} °C`} />
          <StatTile label="Unmet-cooling hours" value={fmt(res.summary.thermal.unmet_hours)} unit="h" sub="must be zero for a feasible design" />
        </div>
      )}
      <Section title="Peak-load performance">
        <p className="text-[12px] text-ink-2">
          Peak loads coincide with the hottest hours, when free cooling is least available; the mechanical plant is sized for the installed IT capacity at the
          dataset's worst hour in every scenario ({fmt(res?.summary.sizing.q_design_kw)} kW, {res?.summary.sizing.units}). N+1 check at the design hour:{" "}
          {res?.summary.failure.n_minus_1.passed ? "✓ passed" : "⛔ failed"}.
        </p>
      </Section>
      {base.error && <Callout level="error">{String(base.error)}</Callout>}
    </div>
  );
}

/** Lighter step of a mode colour for "with mechanical trim" (mixed toward the surface). */
function shade(hex: string) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  const mix = (c: number) => Math.round(c + (255 - c) * 0.45);
  const r = mix((n >> 16) & 255), g = mix((n >> 8) & 255), b = mix(n & 255);
  return `#${((r << 16) | (g << 8) | b).toString(16).padStart(6, "0")}`;
}
