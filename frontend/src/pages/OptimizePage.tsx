import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useJob } from "../api/hooks";
import { ChartCard } from "../charts/ChartCard";
import { intervals, scatter, tornado } from "../charts/options";
import { useTokens } from "../charts/useTokens";
import { Callout, Empty, Field, JobBar, Section, StatTile } from "../components/ui";
import { fmt, pct } from "../lib/format";
import { bestScenario, useProjectPage } from "../lib/project";

interface Evaluation { params: Record<string, number>; feasible: boolean; reasons: string[]; metrics: Record<string, number | null>; config_hash: string }
interface OptResult { mode: string; objective: string; notes: string[]; best: Evaluation | null; pareto_indices: number[]; evaluations: Evaluation[] }

const OPT_STRATEGIES: [string, string][] = [["stack", "Stack / chimney"], ["eahe", "Earth-air exchanger"], ["envelope", "Envelope"], ["economiser", "Economiser margin"]];
const AXES: Record<string, [string, string]> = { water_m3: ["Water withdrawal", "m³/yr"], co2e_t: ["Operational CO₂e", "t/yr"], land_m2: ["Land use", "m²"], capex: ["Capital cost", "currency"] };
const MC_METRICS: Record<string, [string, string]> = { facility_mwh: ["Facility electricity", "MWh/yr"], cooling_mwh: ["Cooling electricity", "MWh/yr"], pue: ["PUE", "–"], water_m3: ["Water withdrawal", "m³/yr"], co2e_t: ["Operational CO₂e", "t/yr"] };
const short = (path: string) => path.split(".").slice(-1)[0].replace(/_/g, " ");

function useLatestSensitivity(simId: string | null, kind: string, refresh: number) {
  const list = useQuery({
    queryKey: ["sens-list", simId, refresh],
    queryFn: () => api.get<{ id: string; kind: string; status: string }[]>(`/api/simulations/${simId}/uncertainty`),
    enabled: !!simId,
  });
  const latest = list.data?.find((r) => r.kind === kind && r.status === "succeeded");
  return useQuery({ queryKey: ["sens", latest?.id], queryFn: () => api.get<any>(`/api/uncertainty/${latest!.id}`), enabled: !!latest, staleTime: Infinity });
}

export function OptimizePage() {
  const { projectId, results, simId, project } = useProjectPage();
  const t = useTokens();
  const qc = useQueryClient();
  const [strategies, setStrategies] = useState<string[]>(["stack"]);
  const [mode, setMode] = useState<"pareto" | "constrained">("pareto");
  const [objective, setObjective] = useState("facility_mwh");
  const [budget, setBudget] = useState(12);
  const [waterCap, setWaterCap] = useState<string>("");
  const [optJob, setOptJob] = useState<string | null>(null);
  const [optId, setOptId] = useState<string | null>(null);
  const [axis, setAxis] = useState("water_m3");
  const [picked, setPicked] = useState<number | null>(null);
  const [sensJobs, setSensJobs] = useState<Record<string, string>>({});
  const [refresh, setRefresh] = useState(0);
  const [mcMetric, setMcMetric] = useState("facility_mwh");

  const optList = useQuery({ queryKey: ["opt-list", projectId], queryFn: () => api.get<any[]>(`/api/projects/${projectId}/optimizations`) });
  useEffect(() => {
    if (!optId && optList.data?.length) {
      const ok = optList.data.find((o) => o.status === "succeeded");
      if (ok) setOptId(ok.id);
    }
  }, [optList.data, optId]);
  const opt = useQuery({ queryKey: ["opt", optId], queryFn: () => api.get<{ status: string; result: OptResult | null; request: any; created_at: string }>(`/api/optimizations/${optId}`), enabled: !!optId });
  const optJobQ = useJob(optJob);
  useEffect(() => {
    if (optJobQ.data?.status === "succeeded") {
      qc.invalidateQueries({ queryKey: ["opt-list", projectId] });
      qc.invalidateQueries({ queryKey: ["opt", optId] });
      setPicked(null);
    }
  }, [optJobQ.data?.status, optId, projectId, qc]);

  const runOpt = useMutation({
    mutationFn: () => api.post<{ optimization_id: string; job_id: string }>("/api/optimizations", {
      project_id: projectId, dataset_id: results?.dataset_id, mode, objective, strategies, budget, water_cap_m3: waterCap === "" ? null : Number(waterCap), base_on: "S1",
    }),
    onSuccess: (r) => { setOptJob(r.job_id); setOptId(r.optimization_id); },
  });

  const tornadoQ = useLatestSensitivity(simId, "tornado", refresh);
  const mcQ = useLatestSensitivity(simId, "monte_carlo", refresh);
  const rankQ = useLatestSensitivity(simId, "rank_stability", refresh);
  const tJob = useJob(sensJobs.tornado);
  const mJob = useJob(sensJobs.monte_carlo);
  const rJob = useJob(sensJobs.rank_stability);
  const doneKey = [tJob.data?.status, mJob.data?.status, rJob.data?.status].join();
  useEffect(() => {
    if (doneKey.includes("succeeded")) setRefresh((x) => x + 1);
  }, [doneKey]);
  const best = bestScenario(results?.comparison);
  const runSens = useMutation({
    mutationFn: (kind: string) => api.post<{ job_id: string }>("/api/uncertainty", {
      simulation_id: simId, kind, scenario_id: best, n: kind === "monte_carlo" ? 16 : 8, n_category: 4,
      compare: kind === "rank_stability" ? Object.keys(results?.comparison ?? {}).filter((k) => k !== "S8") : undefined,
    }).then((r) => ({ kind, job: r.job_id })),
    onSuccess: ({ kind, job }) => setSensJobs((j) => ({ ...j, [kind]: job })),
  });

  const onPointClick = useCallback((p: any) => { if (Array.isArray(p?.value)) setPicked(Number(p.value[2])); }, []);
  const res = opt.data?.result ?? null;
  const evals = useMemo(() => res?.evaluations ?? [], [res]);
  const pareto = new Set(res?.pareto_indices ?? []);
  const vars = evals.length ? Object.keys(evals[0].params) : [];
  const pt = (e: Evaluation, i: number) => [e.metrics[axis] ?? NaN, e.metrics.facility_mwh ?? NaN, i] as [number, number, number];
  const groups = [
    { name: "Pareto-optimal (all objectives)", data: evals.map(pt).filter((p) => pareto.has(p[2]) && Number.isFinite(p[0])), color: t.series[0], size: 11 },
    { name: "Feasible, dominated", data: evals.map(pt).filter((p) => !pareto.has(p[2]) && evals[p[2]].feasible && Number.isFinite(p[0])), color: t.neutral, size: 8 },
    { name: "Infeasible (constraint)", data: evals.map(pt).filter((p) => !evals[p[2]].feasible && Number.isFinite(p[0]) && Number.isFinite(p[1])), color: t.series[7], size: 8 },
  ].filter((g) => g.data.length);
  const axisMissing = evals.length > 0 && evals.every((e) => e.metrics[axis] === null || e.metrics[axis] === undefined);
  const sel = picked !== null ? evals[picked] : res?.best ?? null;
  const s0 = results?.comparison?.S0?.metrics;
  const s1 = results?.comparison?.S1?.metrics;
  const rec = results?.recommendation;
  const recRows = rec ? Object.values(rec.candidates).sort((a: any, b: any) => rec.ranking.indexOf(a.candidate) - rec.ranking.indexOf(b.candidate) + (a.feasible === b.feasible ? 0 : a.feasible ? -1000 : 1000)) : [];
  const period = results ? `${results.climate_summary?.period.start_utc.slice(0, 10)} – ${results.climate_summary?.period.end_utc.slice(0, 10)}` : "";
  const src = `Second Nature optimisation ${optId?.slice(0, 8) ?? ""} on the project climate dataset`;

  const tor = tornadoQ.data?.result;
  const mc = mcQ.data?.result;
  const mcm = mc?.metrics?.[mcMetric];
  const busy = (j?: { status: string }) => !!j && ["queued", "running"].includes(j.status);

  if (!project.data) return <p className="text-ink-2">Loading…</p>;
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-[20px] font-semibold">Optimisation and uncertainty</h1>
        <p className="text-[12px] text-ink-2 max-w-4xl">
          Designs are built on the improved-conventional plant (S1) and simulated hourly. Thermal limits, unmet load and any water cap are hard
          constraints: an infeasible design is never ranked, whatever its energy. The Pareto set is non-dominated over facility energy, water,
          carbon, land and complexity (and capital cost when supplied).
        </p>
      </div>

      <Section title="Recommended configuration">
        {!results || !rec ? <Callout>Run the scenarios first; the recommendation is computed from the simulation, not from climate rules of thumb.</Callout> : (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <StatTile label="Best scenario (weighted objective)" value={best ?? "n/a"} sub={best ? results.comparison?.[best]?.label : undefined} />
              <StatTile label="Top-ranked single strategy" value={rec.ranking[0] ?? "none"} sub={rec.candidates[rec.ranking[0]]?.explanation?.verdict} />
              <StatTile label={`PUE ${best ?? ""}`} value={fmt(results.comparison?.[best ?? "S0"]?.metrics.pue, 3)} delta={results.comparison?.[best ?? "S0"]?.delta_pct.pue} />
              <StatTile label={`Facility electricity ${best ?? ""}`} value={fmt(results.comparison?.[best ?? "S0"]?.metrics.facility_mwh)} unit="MWh/yr" delta={results.comparison?.[best ?? "S0"]?.delta_pct.facility_mwh} />
            </div>
            <div className="card overflow-auto">
              <table className="w-full text-[12px] tnum">
                <thead>
                  <tr className="border-b border-line text-left">
                    {["Strategy (on S1)", "Feasible", "Energy saved", "Water change", "CO₂e avoided", "Peak change", "Score", "Confidence", "Verdict / constraint"].map((h) => <th key={h} className="px-2 py-1.5 font-semibold">{h}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {recRows.map((r: any) => (
                    <tr key={r.candidate} className="border-b border-line align-top">
                      <td className="px-2 py-1 font-medium">{r.candidate}</td>
                      <td className="px-2 py-1">{r.feasible ? "✓ yes" : "✗ no"}</td>
                      <td className="px-2 py-1">{r.feasible ? `${fmt(r.energy_saved_mwh)} MWh (${fmt(r.energy_saved_pct, 1)} %)` : "–"}</td>
                      <td className="px-2 py-1">{r.feasible ? `${r.water_change_m3 >= 0.5 ? "+" : ""}${fmt(r.water_change_m3)} m³` : "–"}</td>
                      <td className="px-2 py-1">{r.feasible ? (r.carbon_avoided_t == null ? "n/a" : `${fmt(r.carbon_avoided_t)} t`) : "–"}</td>
                      <td className="px-2 py-1">{r.feasible ? `${fmt(-r.peak_reduction_kw)} kW` : "–"}</td>
                      <td className="px-2 py-1">{r.feasible ? fmt(r.score, 2) : "–"}</td>
                      <td className="px-2 py-1" title={r.confidence?.notes?.join("; ")}>{r.feasible ? fmt(r.confidence?.score, 2) : "–"}</td>
                      <td className="px-2 py-1 max-w-[420px]">{r.feasible ? r.explanation?.verdict : r.infeasible_reasons?.join("; ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="text-[11px] text-ink-3 p-2">{rec.method}. Weights: {Object.entries(rec.weights).map(([k, v]) => `${k} ${v}`).join(", ")}.</p>
            </div>
          </>
        )}
      </Section>

      <Section title="Design optimisation">
        <div className="card p-4 grid md:grid-cols-2 xl:grid-cols-6 gap-3 items-end">
          <fieldset className="xl:col-span-2">
            <legend className="label mb-1">Strategies to size</legend>
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {OPT_STRATEGIES.map(([k, l]) => (
                <label key={k} className="flex items-center gap-1.5 text-[13px]">
                  <input type="checkbox" checked={strategies.includes(k)} onChange={(e) => setStrategies((s) => (e.target.checked ? [...s, k] : s.filter((x) => x !== k)))} />{l}
                </label>
              ))}
            </div>
          </fieldset>
          <Field label="Mode">
            <select className="input" value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
              <option value="pareto">Pareto (multi-objective)</option><option value="constrained">Single objective, constrained</option>
            </select>
          </Field>
          <Field label="Objective (best design)">
            <select className="input" value={objective} onChange={(e) => setObjective(e.target.value)}>
              <option value="facility_mwh">Facility electricity</option><option value="cooling_mwh">Cooling electricity</option><option value="pue">PUE</option><option value="co2e_t">Operational CO₂e</option>
            </select>
          </Field>
          <Field label="Evaluation budget" help="Each design is a full hourly simulation"><input className="input tnum" type="number" min={2} max={200} value={budget} onChange={(e) => setBudget(Math.max(2, Number(e.target.value) || 2))} /></Field>
          <Field label="Water cap" unit="m³/yr" help="Blank = no cap"><input className="input tnum" type="number" min={0} value={waterCap} onChange={(e) => setWaterCap(e.target.value)} /></Field>
          <div className="xl:col-span-6 flex flex-wrap gap-2 items-center">
            <button className="btn btn-primary" disabled={!strategies.length || runOpt.isPending || busy(optJobQ.data) || !results} onClick={() => runOpt.mutate()}>Run optimisation</button>
            {!results && <span className="text-[12px] text-ink-3">Needs a completed scenario run (for the dataset).</span>}
            {optList.data && optList.data.length > 0 && (
              <select className="input !w-auto" value={optId ?? ""} onChange={(e) => { setOptId(e.target.value); setPicked(null); }} aria-label="Previous optimisation runs">
                {optList.data.map((o) => <option key={o.id} value={o.id}>{o.created_at.slice(0, 16).replace("T", " ")} · {(o.strategies ?? []).join("+")} · {o.mode} · {o.status}</option>)}
              </select>
            )}
            {optJobQ.data && optJobQ.data.id === optJob && <div className="min-w-[260px]"><JobBar job={optJobQ.data} label="Optimisation" /></div>}
            {runOpt.error && <Callout level="error">{String(runOpt.error.message)}</Callout>}
          </div>
        </div>

        {res && (
          <>
            {res.notes.length > 0 && <p className="text-[11px] text-ink-3">{res.notes.join(" · ")}</p>}
            <div className="grid xl:grid-cols-[1fr_380px] gap-4">
              <div className="flex flex-col gap-2">
                <div className="flex gap-2 flex-wrap text-[12px]" role="group" aria-label="Trade-off axis">
                  {Object.entries(AXES).map(([k, [l]]) => <button key={k} className={`btn !py-1 !px-2 ${axis === k ? "btn-primary" : ""}`} aria-pressed={axis === k} onClick={() => setAxis(k)}>Energy vs {l.toLowerCase()}</button>)}
                </div>
                <ChartCard id="c32-pareto" number={32} title={`Energy–${AXES[axis][0].toLowerCase()} trade-off of tested designs`} units={`facility MWh/yr vs ${AXES[axis][1]}`} period={period} source={src}
                  assumptions="Pareto status is computed over all objectives, so a Pareto point can look dominated in this 2-D view. Click a point to compare it with the baseline."
                  empty={axisMissing ? (axis === "capex" ? "Capital costs not supplied — cost axis unavailable." : axis === "co2e_t" ? "No grid emission factor — carbon axis unavailable." : "No values for this axis.") : !groups.length ? "No evaluations." : undefined}
                  height={340}
                  onChartClick={onPointClick}
                  option={groups.length && !axisMissing ? {
                    ...scatter(t, { series: groups, xName: `${AXES[axis][0]} (${AXES[axis][1]})`, yName: "MWh/yr", xUnit: AXES[axis][1], yUnit: "MWh/yr",
                      tooltipExtra: (v) => { const e = evals[v[2] as number]; return `<div style="font-size:11px;opacity:.8">${Object.entries(e.params).map(([k, x]) => `${short(k)} = ${x}`).join("<br/>")}${e.reasons.length ? `<br/>✗ ${e.reasons.join("; ")}` : ""}</div>`; } }),
                  } : undefined}
                  table={{ columns: ["#", ...vars.map(short), "feasible", "pareto", "facility_mwh", "water_m3", "co2e_t", "land_m2", "capex", "reasons"],
                    rows: evals.map((e, i) => [i, ...vars.map((v) => e.params[v]), e.feasible, pareto.has(i), e.metrics.facility_mwh, e.metrics.water_m3, e.metrics.co2e_t, e.metrics.land_m2, e.metrics.capex, e.reasons.join("; ")]) }} />
                <ChartPick evals={evals} onPick={setPicked} picked={picked} />
              </div>
              <div className="card p-4 flex flex-col gap-2 text-[12px]">
                <div className="font-semibold text-[13px]">{picked !== null ? `Selected design #${picked}` : "Best feasible design"} vs baseline</div>
                {!sel ? <Callout level="warning">No feasible design among those tested.</Callout> : (
                  <>
                    <dl className="grid grid-cols-[1fr_auto] gap-x-2 tnum">
                      {Object.entries(sel.params).map(([k, v]) => <div key={k} className="contents"><dt className="text-ink-2">{short(k)}</dt><dd className="text-right font-medium">{String(v)}</dd></div>)}
                    </dl>
                    <table className="w-full tnum mt-2">
                      <thead><tr className="border-b border-line"><th className="text-left py-1">Metric</th><th className="text-right">Design</th><th className="text-right">S0</th><th className="text-right">Δ vs S0</th></tr></thead>
                      <tbody>
                        {(["facility_mwh", "cooling_mwh", "pue", "water_m3", "co2e_t", "peak_facility_kw", "unmet_hours"] as const).map((m) => {
                          const key0 = m === "water_m3" ? "water_withdrawal_m3" : m;
                          const v = sel.metrics[m];
                          const b = s0?.[key0];
                          return (
                            <tr key={m} className="border-b border-line">
                              <td className="py-0.5 text-ink-2">{m}</td>
                              <td className="text-right">{fmt(v, m === "pue" ? 3 : 0)}</td>
                              <td className="text-right">{fmt(b, m === "pue" ? 3 : 0)}</td>
                              <td className="text-right">{v != null && b ? pct((100 * (v - b)) / b) : "n/a"}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                    {s1 && <p className="text-ink-3">S1 (improved conventional, the base of every tested design): {fmt(s1.facility_mwh)} MWh/yr, PUE {fmt(s1.pue, 3)}.</p>}
                    {!sel.feasible && <Callout level="error" title="Infeasible">{sel.reasons.join("; ")}</Callout>}
                  </>
                )}
              </div>
            </div>
          </>
        )}
        {!res && opt.data?.status === "failed" && <Callout level="error">The selected optimisation failed.</Callout>}
      </Section>

      <Section title="Uncertainty and sensitivity" right={
        <div className="flex gap-2">
          {(["tornado", "monte_carlo", "rank_stability"] as const).map((k) => (
            <button key={k} className="btn" disabled={!results || runSens.isPending || busy({ tornado: tJob, monte_carlo: mJob, rank_stability: rJob }[k].data)} onClick={() => runSens.mutate(k)}>
              Run {k === "monte_carlo" ? "Monte Carlo" : k === "rank_stability" ? "rank stability" : "tornado"}
            </button>
          ))}
        </div>
      }>
        {!results && <Empty title="No simulation">Uncertainty analysis re-runs the baseline and the best design with perturbed inputs; it needs a completed scenario run.</Empty>}
        {runSens.error && <Callout level="error">{String(runSens.error.message)}</Callout>}
        <div className="grid md:grid-cols-3 gap-2">
          {tJob.data && <JobBar job={tJob.data} label="Tornado" />}
          {mJob.data && <JobBar job={mJob.data} label="Monte Carlo" />}
          {rJob.data && <JobBar job={rJob.data} label="Rank stability" />}
        </div>
        {results && (
          <div className="grid xl:grid-cols-2 gap-4">
            <ChartCard id="c34-tornado" number={34} title={`Sensitivity of facility-energy saving (${tor?.design_scenario ?? best ?? "design"} vs S0)`} units="MWh/yr change in saving relative to the central estimate"
              period={period} source={`Second Nature one-at-a-time sensitivity ${tornadoQ.data?.id?.slice(0, 8) ?? ""}`}
              assumptions={`${tor?.method ?? "Each input set to its low and high value, others central"}. Central saving ${tor ? fmt(tor.central_saving) : "–"} MWh/yr. Warming offsets are sensitivity tests, not climate projections.`}
              empty={!tor ? "No tornado analysis yet — click “Run tornado” (≈ 2 + 4 × inputs simulations)." : undefined}
              height={360}
              option={tor ? tornado(t, { rows: tor.rows.map((r: any) => ({ label: `${r.label} (${r.low_value}–${r.high_value})`, low: r.low_saving, high: r.high_saving })), center: tor.central_saving, unit: "MWh/yr", xName: "Δ saving vs central (MWh/yr)" }) : undefined}
              table={tor ? { columns: ["input", "category", "low_value", "high_value", "low_saving_mwh", "high_saving_mwh", "swing_mwh"], rows: tor.rows.map((r: any) => [r.label, r.category, r.low_value, r.high_value, r.low_saving, r.high_saving, r.swing_saving]) } : undefined} />
            <div className="flex flex-col gap-2">
              <ChartCard id="c33-intervals" number={33} title={`Uncertainty interval — ${MC_METRICS[mcMetric][0].toLowerCase()}`} units={`${MC_METRICS[mcMetric][1]}, median with P5–P95`}
                period={period} source={`Second Nature paired Monte Carlo ${mcQ.data?.id?.slice(0, 8) ?? ""} (n = ${mc?.n ?? "–"})`}
                assumptions="Common random numbers for baseline and design; inputs sampled from the triangular/uniform ranges in the uncertainty register. Small n gives a screening estimate only."
                empty={!mc ? "No Monte Carlo run yet — click “Run Monte Carlo”." : !mcm ? "This metric is unavailable (e.g. no grid emission factor)." : undefined}
                option={mcm ? intervals(t, { categories: ["S0 baseline", `${mc.design_scenario} design`], p5: [mcm.baseline.p5, mcm.design.p5], p50: [mcm.baseline.p50, mcm.design.p50], p95: [mcm.baseline.p95, mcm.design.p95], unit: MC_METRICS[mcMetric][1], yName: MC_METRICS[mcMetric][1] }) : undefined}
                table={mc ? { columns: ["metric", "baseline_p5", "baseline_p50", "baseline_p95", "design_p5", "design_p50", "design_p95", "saving_p5", "saving_p50", "saving_p95", "prob_design_better"],
                  rows: Object.entries(mc.metrics).map(([k, v]: [string, any]) => [k, v.baseline.p5, v.baseline.p50, v.baseline.p95, v.design.p5, v.design.p50, v.design.p95, v.saving.p5, v.saving.p50, v.saving.p95, v.prob_design_better]) } : undefined} />
              {mc && (
                <>
                  <div className="flex gap-2 flex-wrap text-[12px]" role="group" aria-label="Interval metric">
                    {Object.entries(MC_METRICS).filter(([k]) => mc.metrics[k]).map(([k, [l]]) => <button key={k} className={`btn !py-1 !px-2 ${mcMetric === k ? "btn-primary" : ""}`} aria-pressed={mcMetric === k} onClick={() => setMcMetric(k)}>{l}</button>)}
                  </div>
                  {mcm && (
                    <p className="text-[12px] text-ink-2">
                      Saving (S0 − design): median {fmt(mcm.saving.p50, mcMetric === "pue" ? 3 : 0)}, P5–P95 {fmt(mcm.saving.p5, mcMetric === "pue" ? 3 : 0)} to {fmt(mcm.saving.p95, mcMetric === "pue" ? 3 : 0)} {MC_METRICS[mcMetric][1]};
                      design better in {fmt((mcm.prob_design_better ?? 0) * 100)} % of samples. Samples with a thermal violation: {mc.violations_in_samples}.
                    </p>
                  )}
                  {mc.by_category && (
                    <div className="card p-3 text-[12px]">
                      <div className="font-semibold mb-1">Spread of facility-energy saving by uncertainty source</div>
                      <table className="w-full tnum"><tbody>
                        {Object.entries(mc.by_category).map(([c, v]: [string, any]) => (
                          <tr key={c} className="border-b border-line"><td className="py-0.5 text-ink-2">{c}</td><td className="text-right">σ {fmt(v.saving_std_mwh)} MWh/yr</td><td className="text-right">P5–P95 {fmt(v.p5)} – {fmt(v.p95)}</td></tr>
                        ))}
                      </tbody></table>
                      <p className="text-ink-3 mt-1">Each source varied alone with the others at central values (climate year, parameters, model form, missing data are kept separate).</p>
                    </div>
                  )}
                </>
              )}
            </div>
            {rankQ.data?.result && (
              <div className="card p-4 text-[12px] xl:col-span-2">
                <div className="font-semibold text-[13px] mb-1">Rank stability (n = {rankQ.data.result.n}, metric {rankQ.data.result.metric})</div>
                <div className="flex flex-wrap gap-4 tnum">
                  {Object.entries(rankQ.data.result.probability_best as Record<string, number>).sort((a, b) => b[1] - a[1]).map(([k, p]) => <span key={k}><b>{k}</b> best in {fmt(p * 100)} % of samples</span>)}
                </div>
                <p className="text-ink-3 mt-1">Leader: {rankQ.data.result.leader}. {rankQ.data.result.samples_where_leader_loses.length} sampled conditions change the ranking (listed in the JSON export).</p>
              </div>
            )}
          </div>
        )}
      </Section>
    </div>
  );
}

/** Keyboard-accessible design picker mirroring the scatter (click targets on a chart are not enough on their own). */
function ChartPick({ evals, picked, onPick }: { evals: Evaluation[]; picked: number | null; onPick: (i: number | null) => void }) {
  if (!evals.length) return null;
  return (
    <Field label="Select a tested design to compare with the baseline">
      <select className="input" value={picked ?? ""} onChange={(e) => onPick(e.target.value === "" ? null : Number(e.target.value))}>
        <option value="">Best feasible design</option>
        {evals.map((e, i) => (
          <option key={i} value={i}>#{i} · {Object.entries(e.params).map(([k, v]) => `${short(k)} ${v}`).join(", ")} · {e.feasible ? `${fmt(e.metrics.facility_mwh)} MWh` : "infeasible"}</option>
        ))}
      </select>
    </Field>
  );
}
