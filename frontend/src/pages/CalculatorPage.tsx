import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { api } from "../api/client";
import { ChartCard } from "../charts/ChartCard";
import { lineChart } from "../charts/options";
import { useTokens } from "../charts/useTokens";
import { Callout, Field, Section, Slider } from "../components/ui";
import { fmt } from "../lib/format";
import { useProjectPage } from "../lib/project";
import { useDebounced } from "../lib/useDebounced";

/** POST calculator call keyed by its (debounced) inputs; previous result stays visible while the next one computes. */
function useCalc<T>(path: string, body: unknown) {
  const b = useDebounced(body, 250);
  return useQuery({ queryKey: ["calc", path, b], queryFn: () => api.post<T>(`/api/calculators/${path}`, b), placeholderData: keepPreviousData, retry: false });
}

function Out({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(140px,1fr)_auto] gap-x-3 gap-y-0.5 text-[12px] tnum">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-ink-2">{k}</dt>
          <dd className="text-right font-medium">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function Eqs({ eq }: { eq?: Record<string, string> | string }) {
  if (!eq) return null;
  const items = typeof eq === "string" ? [["", eq]] : Object.entries(eq);
  return (
    <div className="text-[11px] text-ink-3 font-mono leading-relaxed mt-1">
      {items.map(([k, v]) => <div key={k}>{k ? `${k}: ` : ""}{v}</div>)}
    </div>
  );
}

const Err = ({ e }: { e: unknown }) => (e ? <Callout level="error">{String((e as Error).message)}</Callout> : null);

function PsychroCalc({ elevation }: { elevation: number }) {
  const [tdb, setTdb] = useState(30);
  const [rh, setRh] = useState(50);
  const q = useCalc<any>("psychrometrics", { t_db_c: tdb, rh_pct: rh, elevation_m: elevation });
  const d = q.data;
  return (
    <div className="card p-4 flex flex-col gap-3">
      <h3 className="font-semibold text-[14px]">Moist-air state</h3>
      <Slider label="Dry bulb" value={tdb} min={-20} max={50} step={0.5} unit="°C" onChange={setTdb} />
      <Slider label="Relative humidity" value={rh} min={1} max={100} step={1} unit="%" onChange={setRh} />
      <Err e={q.error} />
      {d && (
        <Out rows={[
          ["Station pressure", `${fmt(d.p_pa)} Pa (${fmt(elevation)} m)`],
          ["Humidity ratio W", `${fmt(d.w * 1000, 2)} g/kg_da`],
          ["Dew point", `${fmt(d.t_dp_c, 2)} °C`],
          ["Wet bulb", `${fmt(d.t_wb_c, 2)} °C`],
          ["Enthalpy", `${fmt(d.h_jkg / 1000, 2)} kJ/kg_da`],
          ["Density", `${fmt(d.rho_kgm3, 4)} kg/m³`],
          ["Specific volume", `${fmt(d.v_m3kg, 4)} m³/kg_da`],
          ["Saturation pressure p_ws", `${fmt(d.p_ws_pa, 1)} Pa`],
          ["Vapour pressure p_w", `${fmt(d.p_w_pa, 1)} Pa`],
          ["Humid c_p", `${fmt(d.cp_humid_j_kgk, 1)} J/(kg·K)`],
        ]} />
      )}
      <Eqs eq={d?.equations} />
    </div>
  );
}

function AirflowCalc({ it }: { it: Record<string, any> }) {
  const [load, setLoad] = useState(it.installed_capacity_mw * 1000 * it.average_utilisation);
  const [dt, setDt] = useState(it.server_delta_t_k);
  const q = useCalc<any>("airflow-requirement", { it_load_kw: load, delta_t_k: dt, rack_count: it.rack_count, max_rack_kw: it.max_rack_kw, t_supply_c: it.inlet_setpoint_c });
  const d = q.data;
  return (
    <div className="card p-4 flex flex-col gap-3">
      <h3 className="font-semibold text-[14px]">Server airflow requirement</h3>
      <Slider label="IT heat to air" value={Math.round(load)} min={100} max={Math.max(20000, Math.round(load))} step={50} unit="kW" onChange={setLoad} />
      <Slider label="Server ΔT" value={dt} min={5} max={25} step={0.5} unit="K" onChange={setDt} />
      <Err e={q.error} />
      {d && (
        <Out rows={[
          ["Mass flow", `${fmt(d.mass_flow_kg_s, 1)} kg/s`],
          ["Volume flow at supply", `${fmt(d.volume_flow_m3s, 1)} m³/s`],
          [`Per rack (${it.rack_count} racks)`, `${fmt(d.per_rack_m3s, 3)} m³/s (${fmt(d.per_rack_cfm)} CFM)`],
          ...(d.peak_rack_m3s !== undefined ? [[`Peak rack (${it.max_rack_kw} kW)`, `${fmt(d.peak_rack_m3s, 3)} m³/s`] as [string, string]] : []),
        ]} />
      )}
      <Eqs eq={d?.equation} />
    </div>
  );
}

function StackCalc({ elevation, required, t }: { elevation: number; required: number; t: ReturnType<typeof useTokens> }) {
  const [p, setP] = useState({ t_inside_c: 36, t_outside_c: 18, inlet_area_m2: 120, outlet_area_m2: 120, discharge_coefficient: 0.6, extra_loss_k: 2, wind_ms: 0, required_flow_m3s: Math.round(required) });
  const set = (k: keyof typeof p) => (v: number) => setP((x) => ({ ...x, [k]: v }));
  const q = useCalc<any>("stack", { ...p, elevation_m: elevation, heights_m: [5, 10, 15, 20, 25, 30, 40, 50, 60, 80] });
  const rows: any[] = q.data?.rows ?? [];
  return (
    <div className="grid lg:grid-cols-[320px_1fr] gap-4">
      <div className="card p-4 flex flex-col gap-3">
        <h3 className="font-semibold text-[14px]">Stack / chimney inputs</h3>
        <Slider label="Exhaust (hot-aisle) temperature" value={p.t_inside_c} min={20} max={50} step={0.5} unit="°C" onChange={set("t_inside_c")} />
        <Slider label="Outdoor temperature" value={p.t_outside_c} min={-10} max={45} step={0.5} unit="°C" onChange={set("t_outside_c")} />
        <Slider label="Inlet free area" value={p.inlet_area_m2} min={5} max={400} step={5} unit="m²" onChange={set("inlet_area_m2")} />
        <Slider label="Outlet free area" value={p.outlet_area_m2} min={5} max={400} step={5} unit="m²" onChange={set("outlet_area_m2")} />
        <Slider label="Discharge coefficient C_d" value={p.discharge_coefficient} min={0.3} max={0.9} step={0.01} onChange={set("discharge_coefficient")} />
        <Slider label="Additional loss ΣK" value={p.extra_loss_k} min={0} max={10} step={0.1} onChange={set("extra_loss_k")} />
        <Slider label="Reference wind (10 m)" value={p.wind_ms} min={0} max={15} step={0.5} unit="m/s" onChange={set("wind_ms")} />
        <Slider label="Required airflow" value={p.required_flow_m3s} min={0} max={Math.max(800, p.required_flow_m3s)} step={5} unit="m³/s" onChange={set("required_flow_m3s")} />
        <Err e={q.error} />
        {q.data && <Out rows={[["ρ outside / inside", `${fmt(q.data.rho_outside, 4)} / ${fmt(q.data.rho_inside, 4)} kg/m³`], ["Loss coefficient K", fmt(q.data.loss_coefficient_k, 2)]]} />}
        <Eqs eq={q.data?.equations} />
      </div>
      <div className="flex flex-col gap-3">
        <ChartCard id="c14-stack" number={14} title="Chimney height versus natural airflow" units="m³/s" period="steady state at the inputs shown"
          source="Second Nature two-opening airflow network (ASHRAE wind profile, orifice equation)"
          assumptions={`Single inlet and outlet; ${p.wind_ms > 0 ? "wind Cp +0.6 inlet / −0.5 outlet" : "no wind"}; the line marks the required server airflow.`}
          empty={!rows.length ? (q.isLoading ? "Computing…" : "No result.") : undefined}
          option={rows.length ? lineChart(t, { xValue: { name: "Chimney height above inlet (m)", unit: "m" }, yName: "m³/s", unit: "m³/s", yMin: 0,
            series: [{ name: "Natural airflow", data: rows.map((r) => [r.height_m, r.volume_flow_m3s]), area: true }],
            markLines: p.required_flow_m3s > 0 ? [{ y: p.required_flow_m3s, label: "required" }] : undefined }) : undefined}
          table={{ columns: ["height_m", "stack_dp_pa", "wind_dp_pa", "mass_flow_kg_s", "volume_flow_m3s", "heat_removed_kw", "sufficient", "reversed"],
            rows: rows.map((r) => [r.height_m, r.stack_dp_pa, r.wind_dp_pa, r.mass_flow_kg_s, r.volume_flow_m3s, r.heat_removed_kw, r.sufficient, r.reversed]) }} />
        {q.data?.warning && <Callout level="warning">{q.data.warning}</Callout>}
      </div>
    </div>
  );
}

function EAHECalc({ cfg, t }: { cfg: Record<string, any>; t: ReturnType<typeof useTokens> }) {
  const e = cfg.strategies.eahe;
  const [p, setP] = useState({ n_pipes: e.n_pipes, length_m: e.length_m, diameter_m: e.diameter_m, depth_m: e.depth_m, spacing_m: e.spacing_m, flow_m3s: e.design_flow_m3s,
    t_in_c: 32, rh_pct: 40, t_ground_c: 15, soil_k: e.soil_k ?? 1.3, hours: 720 });
  const [sweep, setSweep] = useState<"length" | "diameter" | "flow" | "depth">("length");
  const set = (k: keyof typeof p) => (v: number) => setP((x) => ({ ...x, [k]: v }));
  const q = useCalc<any>("earth-air", { ...p, sweep });
  const d = q.data;
  const sw: any[] = d?.sweep?.rows ?? [];
  const unit = { length: "m", diameter: "m", flow: "m³/s", depth: "m" }[sweep];
  const sweepLabel = { length: "Pipe length", diameter: "Pipe diameter", flow: "Total airflow", depth: "Burial depth" }[sweep];
  return (
    <div className="grid lg:grid-cols-[320px_1fr] gap-4">
      <div className="card p-4 flex flex-col gap-3">
        <h3 className="font-semibold text-[14px]">Earth-air exchanger inputs</h3>
        <Slider label="Pipes" value={p.n_pipes} min={1} max={200} step={1} onChange={set("n_pipes")} />
        <Slider label="Pipe length" value={p.length_m} min={10} max={200} step={5} unit="m" onChange={set("length_m")} />
        <Slider label="Diameter" value={p.diameter_m} min={0.1} max={1.5} step={0.05} unit="m" onChange={set("diameter_m")} />
        <Slider label="Burial depth" value={p.depth_m} min={1} max={8} step={0.25} unit="m" onChange={set("depth_m")} />
        <Slider label="Total airflow" value={p.flow_m3s} min={1} max={200} step={1} unit="m³/s" onChange={set("flow_m3s")} />
        <Slider label="Inlet air (constant)" value={p.t_in_c} min={-10} max={45} step={0.5} unit="°C" onChange={set("t_in_c")} />
        <Slider label="Undisturbed ground" value={p.t_ground_c} min={0} max={30} step={0.5} unit="°C" onChange={set("t_ground_c")} />
        <Slider label="Soil conductivity" value={p.soil_k} min={0.3} max={3.5} step={0.05} unit="W/(m·K)" onChange={set("soil_k")} />
        <Slider label="Continuous operation" value={p.hours} min={24} max={2160} step={24} unit="h" onChange={set("hours")} />
        <Field label="Sweep variable (charts 15–16)">
          <select className="input" value={sweep} onChange={(ev) => setSweep(ev.target.value as typeof sweep)}>
            <option value="length">pipe length</option><option value="diameter">diameter</option><option value="flow">airflow</option><option value="depth">depth</option>
          </select>
        </Field>
        <Err e={q.error} />
        {d && (
          <Out rows={[
            ["Outlet, first hour", `${fmt(d.first_hour.t_out_c, 2)} °C · ${fmt(d.first_hour.q_kw)} kW`],
            [`Outlet, hour ${p.hours}`, `${fmt(d.last_hour.t_out_c, 2)} °C · ${fmt(d.last_hour.q_kw)} kW`],
            ["Air velocity", `${fmt(d.velocity_ms, 2)} m/s`],
            ["Pressure drop", `${fmt(d.pressure_drop_pa)} Pa`],
            ["Fan power", `${fmt(d.fan_power_kw, 1)} kW`],
            ["Net cooling − fan, last hour", `${fmt(d.net_cooling_minus_fan_kw_last, 1)} kW`],
            ["Condensate", `${fmt(d.condensate_kg_total)} kg`],
            ["Field footprint", `${fmt(d.footprint_m2)} m²`],
            ["Mean h / NTU", `${fmt(d.diagnostics.h_mean_w_m2k, 1)} W/(m²·K) · ${fmt(d.diagnostics.ntu_mean, 3)}`],
            ["Shape factor S/L", `${fmt(d.diagnostics.shape_factor_per_m, 3)}`],
            ["Damping depth", `${fmt(d.diagnostics.damping_depth_m, 2)} m`],
          ]} />
        )}
        <Eqs eq={d?.equations} />
      </div>
      <div className="grid xl:grid-cols-2 gap-3 content-start">
        <ChartCard id="c15-eahe-sweep" number={15} title={`Earth-air ${sweepLabel.toLowerCase()} versus sensible cooling`} units="kW"
          period={`after ${Math.min(p.hours, 240)} h of continuous operation`} source="Second Nature transient EAHE model (segmented air side, radial FD soil)"
          assumptions="Constant inlet air and undisturbed ground temperature; soil properties as entered."
          empty={!sw.length ? (q.isLoading ? "Computing…" : "No result.") : undefined}
          option={sw.length ? lineChart(t, { xValue: { name: `${sweepLabel} (${unit})`, unit }, yName: "kW", unit: "kW", yMin: 0, series: [{ name: "Sensible cooling", data: sw.map((r) => [r[sweep], r.q_last_kw]), area: true }] }) : undefined}
          table={{ columns: [`${sweep} (${unit})`, "q_kw", "t_out_c", "fan_kw", "dp_pa", "net_kw"], rows: sw.map((r) => [r[sweep], r.q_last_kw, r.t_out_last_c, r.fan_kw, r.dp_pa, r.net_kw]) }} />
        <ChartCard id="c16-eahe-fan" number={16} title="Fan power versus net cooling" units="kW" period={`after ${Math.min(p.hours, 240)} h`}
          source="Second Nature EAHE hydraulics (Darcy–Weisbach + bends + filter, P = V̇Δp/η)"
          assumptions="Net = sensible cooling − fan electricity (both kW); fan heat is added to the air stream in the full simulation."
          empty={!sw.length ? (q.isLoading ? "Computing…" : "No result.") : undefined}
          option={sw.length ? lineChart(t, { xValue: { name: `${sweepLabel} (${unit})`, unit }, yName: "kW", unit: "kW", series: [
            { name: "Net cooling (cooling − fan)", data: sw.map((r) => [r[sweep], r.net_kw]), color: t.series[0] },
            { name: "Fan power", data: sw.map((r) => [r[sweep], r.fan_kw]), color: t.series[1] },
          ], markLines: [{ y: 0, label: "break-even" }] }) : undefined}
          table={{ columns: [`${sweep} (${unit})`, "net_kw", "fan_kw"], rows: sw.map((r) => [r[sweep], r.net_kw, r.fan_kw]) }} />
        <ChartCard id="c-eahe-outlet" title="Outlet temperature during continuous operation" units="°C" period={`${p.hours} h`} className="xl:col-span-2"
          source="Second Nature transient EAHE model" assumptions="Shows soil saturation: cooling declines as the soil around the pipes warms."
          empty={!d ? "Computing…" : undefined}
          option={d ? lineChart(t, { xValue: { name: "Hours of continuous operation", unit: "h", min: 0 }, yName: "°C", unit: "°C", series: [
            { name: "Inlet air", data: d.series.hour.map((h: number) => [h, p.t_in_c]), color: t.series[1] },
            { name: "Outlet air", data: d.series.hour.map((h: number, i: number) => [h, d.series.t_out_c[i]]), color: t.series[0] },
          ] }) : undefined}
          table={d ? { columns: ["hour", "t_out_c", "q_sensible_kw", "soil_drift_k"], rows: d.series.hour.map((h: number, i: number) => [h, d.series.t_out_c[i], d.series.q_sensible_kw[i], d.series.soil_drift_k[i]]) } : undefined} />
      </div>
    </div>
  );
}

function EnvelopeCalc() {
  const [layers, setLayers] = useState([{ material: "concrete_dense", thickness_m: 0.2 }, { material: "mineral_wool", thickness_m: 0.15 }]);
  const [alpha, setAlpha] = useState(0.6);
  const q = useCalc<any>("envelope", { layers, solar_absorptance: alpha });
  const d = q.data;
  const mats = Object.keys(d?.materials ?? { concrete_dense: 1, mineral_wool: 1 });
  return (
    <div className="card p-4 flex flex-col gap-3">
      <h3 className="font-semibold text-[14px]">Wall / roof construction (outside → inside)</h3>
      {layers.map((l, i) => (
        <div key={i} className="grid grid-cols-[1fr_110px_auto] gap-2 items-end">
          <Field label={`Layer ${i + 1}`}>
            <select className="input" value={l.material} onChange={(e) => setLayers((ls) => ls.map((x, j) => (j === i ? { ...x, material: e.target.value } : x)))}>
              {mats.map((m) => <option key={m} value={m}>{m.replace(/_/g, " ")}</option>)}
            </select>
          </Field>
          <Field label="Thickness" unit="m">
            <input className="input tnum" type="number" min={0.001} step={0.01} value={l.thickness_m}
              onChange={(e) => setLayers((ls) => ls.map((x, j) => (j === i ? { ...x, thickness_m: Number(e.target.value) || 0.001 } : x)))} />
          </Field>
          <button className="btn" aria-label={`Remove layer ${i + 1}`} disabled={layers.length === 1} onClick={() => setLayers((ls) => ls.filter((_, j) => j !== i))}>×</button>
        </div>
      ))}
      <button className="btn self-start" onClick={() => setLayers((ls) => [...ls, { material: mats[0], thickness_m: 0.1 }])}>Add layer</button>
      <Slider label="Outer solar absorptance" value={alpha} min={0.05} max={0.95} step={0.05} onChange={setAlpha} />
      <Err e={q.error} />
      {d && (
        <Out rows={[
          ["U-value", `${fmt(d.u_value, 3)} W/(m²·K)`],
          ["Total resistance", `${fmt(d.r_total_m2k_w, 2)} m²·K/W`],
          ["Periodic transmittance (24 h)", `${fmt(d.periodic_transmittance_w_m2k, 3)} W/(m²·K)`],
          ["Decrement factor", fmt(d.decrement_factor, 3)],
          ["Time lag", `${fmt(d.time_lag_h, 1)} h`],
          ["Areal heat capacity", `${fmt(d.areal_heat_capacity_j_m2k / 1000)} kJ/(m²·K)`],
        ]} />
      )}
      <Eqs eq={d?.equations} />
    </div>
  );
}

export function CalculatorPage() {
  const { project } = useProjectPage();
  const t = useTokens();
  const cfg = project.data?.config;
  // default "required airflow" for the stack calculator: the project's mean IT load through the same endpoint
  const req = useQuery({
    queryKey: ["calc-required", cfg?.it, cfg?.site.elevation_m],
    queryFn: () => api.post<{ volume_flow_m3s: number }>("/api/calculators/airflow-requirement", {
      it_load_kw: cfg!.it.installed_capacity_mw * 1000 * cfg!.it.average_utilisation, delta_t_k: cfg!.it.server_delta_t_k,
      rack_count: cfg!.it.rack_count, t_supply_c: cfg!.it.inlet_setpoint_c, elevation_m: cfg!.site.elevation_m ?? 0 }),
    enabled: !!cfg,
  });
  if (!cfg || !req.data) return <p className="text-ink-2">{req.error ? String(req.error.message) : "Loading…"}</p>;
  const required = req.data.volume_flow_m3s;
  const elevation = cfg.site.elevation_m ?? 0;
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-[20px] font-semibold">Engineering calculator</h1>
        <p className="text-[12px] text-ink-2 max-w-4xl">
          Stand-alone calculations with the same physics modules the hourly simulation uses. Inputs start from the project configuration;
          every result shows its intermediate values and governing equations. These are screening calculations — they do not replace the
          coupled hourly simulation, CFD or a detailed design.
        </p>
      </div>
      <Section title="Psychrometrics and airflow">
        <div className="grid lg:grid-cols-3 gap-4">
          <PsychroCalc elevation={elevation} />
          <AirflowCalc it={cfg.it} />
          <EnvelopeCalc />
        </div>
      </Section>
      <Section title="Stack ventilation (termite mound / solar chimney)"><StackCalc elevation={elevation} required={required} t={t} /></Section>
      <Section title="Earth-air heat exchanger (prairie-dog burrow)"><EAHECalc cfg={cfg} t={t} /></Section>
    </div>
  );
}
