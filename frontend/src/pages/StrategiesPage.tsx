import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useHourly, useJob, usePatchProject, useStrategies } from "../api/hooks";
import { BuildingCutaway } from "../components/BuildingCutaway";
import { ChartCard } from "../charts/ChartCard";
import { barChart, lineChart } from "../charts/options";
import { useTokens } from "../charts/useTokens";
import { Callout, Empty, JobBar, Switch } from "../components/ui";
import { fmt } from "../lib/format";
import { bestScenario, useProjectPage } from "../lib/project";

const CANDIDATE_FOR: Record<string, string[]> = {
  A: ["stack"], B: ["envelope"], C: ["facade_kinetic", "facade_fixed"], D: ["eahe"], E: ["stack"], F: ["storage"], G: ["radiative"], H: ["iec", "evap_precool"], I: [],
};
const CONFIG_KEY: Record<string, string> = { A: "stack", B: "envelope", C: "facade", D: "eahe", E: "stack", F: "storage", G: "radiative", H: "evaporative" };

export function StrategiesPage() {
  const { projectId, project, results, simId } = useProjectPage();
  const lib = useStrategies();
  const t = useTokens();
  const nav = useNavigate();
  const qc = useQueryClient();
  const patch = usePatchProject(projectId);
  const cfg = project.data?.config;
  const [enabled, setEnabled] = useState<Record<string, boolean>>({});
  const [solar, setSolar] = useState(false);
  useEffect(() => {
    if (!cfg) return;
    const e: Record<string, boolean> = {};
    for (const k of ["stack", "envelope", "facade", "eahe", "storage", "radiative", "evaporative", "economiser"]) e[k] = !!cfg.strategies[k]?.enabled;
    setEnabled(e);
    setSolar(!!cfg.strategies.stack?.solar_chimney);
  }, [cfg]);
  const [job, setJob] = useState<{ job: string; sim: string } | null>(null);
  const jobQ = useJob(job?.job);
  const rerun = useMutation({
    mutationFn: async () => {
      const strategies: Record<string, any> = {};
      for (const [k, v] of Object.entries(enabled)) strategies[k] = { enabled: v };
      strategies.stack = { ...(strategies.stack ?? {}), solar_chimney: solar };
      await patch.mutateAsync({ patch: { strategies }, note: "strategy toggles (S8 user-defined)" });
      return api.post<any>("/api/simulations", { project_id: projectId, scenarios: ["S0", "S1", "S8"] });
    },
    onSuccess: (r) => setJob({ job: r.job_id, sim: r.simulation_id }),
  });
  useEffect(() => {
    if (jobQ.data?.status === "succeeded" && job) {
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      nav(`/projects/${projectId}/compare?sim=${job.sim}`);
    }
  }, [jobQ.data?.status, job, nav, projectId, qc]);

  const rec = results?.recommendation;
  const best = bestScenario(results?.comparison);
  const eaheRun = results?.scenario_results?.find((r) => r.summary.eahe) ;
  const stackRun = results?.scenario_results?.find((r) => r.diagnostics.natural_ventilation && r.scenario_id === "S3")
    ?? results?.scenario_results?.find((r) => r.diagnostics.natural_ventilation);
  const nat = useHourly(simId, stackRun?.scenario_id, "time_local,natural_flow_kg_s,m_server_kg_s,natural_sufficient", 24);
  const eaheHourly = useHourly(simId, eaheRun?.scenario_id, "time_local,eahe_soil_drift_k,t_ground_c,eahe_t_out_c,t_oa_c,eahe_flow_m3s", 1);
  const active = useMemo(() => (best ? results?.comparison?.[best]?.strategies ?? [] : []), [best, results]);

  if (!lib.data || !cfg) return <p className="text-ink-2">Loading…</p>;
  const period = results ? `${results.climate_summary?.period.start_utc.slice(0, 10)} – ${results.climate_summary?.period.end_utc.slice(0, 10)}` : "";
  const src = results ? `Second Nature simulation ${simId?.slice(0, 8)} (model ${results.model_version})` : "";

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-[20px] font-semibold">Biomimetic strategy explorer</h1>
        <p className="text-[12px] text-ink-2 max-w-4xl">
          The biological analogy is the inspiration, not proof. Each strategy below is simulated on top of the improved-conventional design (S1) with the
          site's climate; hard constraints are checked before any score. Toggle strategies to build your own design (scenario S8) and re-run.
        </p>
      </div>
      {!results && <Callout level="info">No simulation yet — rankings and savings appear after running scenarios on the Setup page.</Callout>}
      <div className="grid xl:grid-cols-[1fr_440px] gap-4">
        <div className="grid md:grid-cols-2 gap-3">
          {lib.data.strategies.map((s) => {
            const cands = CANDIDATE_FOR[s.id] ?? [];
            const r = cands.map((c) => rec?.candidates?.[c]).find(Boolean);
            const key = CONFIG_KEY[s.id];
            return (
              <article key={s.id} className="card p-4 flex flex-col gap-2" aria-labelledby={`st-${s.id}`}>
                <div className="flex justify-between gap-2 items-start">
                  <div>
                    <div className="text-[11px] text-ink-3">Strategy {s.id} · {s.organism}</div>
                    <h3 id={`st-${s.id}`} className="font-semibold leading-snug">{s.name}</h3>
                  </div>
                  {key && s.id !== "E" && <Switch checked={!!enabled[key]} onChange={(v) => setEnabled((e) => ({ ...e, [key]: v }))} label="in S8" />}
                  {s.id === "E" && <Switch checked={solar} onChange={setSolar} label="solar collector" />}
                </div>
                <p className="text-[12px] text-ink-2">{s.physical_mechanism}</p>
                {r ? (
                  r.feasible ? (
                    <div className="text-[12px] grid grid-cols-2 gap-x-3 gap-y-0.5 tnum">
                      <span className="text-ink-2">Verdict</span><span className="font-semibold">{r.explanation?.verdict}</span>
                      <span className="text-ink-2">Facility energy saved</span><span>{fmt(r.energy_saved_mwh)} MWh/yr ({fmt(r.energy_saved_pct, 1)} %)</span>
                      <span className="text-ink-2">Water change</span><span>{r.water_change_m3 >= 0.5 ? "+" : ""}{fmt(r.water_change_m3)} m³/yr</span>
                      <span className="text-ink-2">CO₂e avoided</span><span>{r.carbon_avoided_t == null ? "n/a (no factor)" : `${fmt(r.carbon_avoided_t)} t/yr`}</span>
                      <span className="text-ink-2">Score / confidence</span><span>{fmt(r.score, 2)} · {fmt(r.confidence?.score, 2)}</span>
                    </div>
                  ) : <Callout level="error" title="Infeasible">{r.infeasible_reasons?.join("; ")}</Callout>
                ) : <p className="text-[12px] text-ink-3">{s.id === "I" ? "The supervisory controller is part of every hybrid scenario." : "Not evaluated in this simulation."}</p>}
                <details className="text-[12px]">
                  <summary className="cursor-pointer text-ink-2">Engineering detail</summary>
                  <dl className="grid grid-cols-[130px_1fr] gap-x-2 gap-y-1 mt-2">
                    <dt className="text-ink-3">Inspiration</dt><dd>{s.biological_inspiration}</dd>
                    <dt className="text-ink-3">Translation</dt><dd>{s.architectural_translation.join(", ")}</dd>
                    <dt className="text-ink-3">Required climate</dt><dd>{s.required_climate}</dd>
                    <dt className="text-ink-3">Geometry</dt><dd>{s.required_geometry}</dd>
                    <dt className="text-ink-3">Auxiliary energy</dt><dd>{s.auxiliary_electricity}</dd>
                    <dt className="text-ink-3">Water</dt><dd>{s.water}</dd>
                    <dt className="text-ink-3">Model</dt><dd>{s.model}</dd>
                    <dt className="text-ink-3">Failure modes</dt><dd>{s.failure_modes.join("; ")}</dd>
                    <dt className="text-ink-3">Maintenance</dt><dd>{s.maintenance}</dd>
                    <dt className="text-ink-3">Constructability</dt><dd>{s.constructability}</dd>
                    <dt className="text-ink-3">Confidence</dt><dd>{s.confidence}</dd>
                    <dt className="text-ink-3">Limitations</dt><dd>{s.limitations}</dd>
                    {r?.explanation?.why_suitable?.length ? <><dt className="text-ink-3">Why (this site)</dt><dd>{r.explanation.why_suitable.join(" ")}</dd></> : null}
                    {r?.explanation?.required_studies?.length ? <><dt className="text-ink-3">Before construction</dt><dd>{r.explanation.required_studies.join("; ")}</dd></> : null}
                  </dl>
                </details>
              </article>
            );
          })}
        </div>
        <div className="flex flex-col gap-3">
          <BuildingCutaway active={active} />
          <p className="text-[11px] text-ink-3">Highlighted: strategies in {best ? `${best} (${results?.comparison?.[best]?.label})` : "the best scenario"}.</p>
          <div className="card p-4 flex flex-col gap-2">
            <div className="font-semibold text-[13px]">User-defined design (S8)</div>
            <Switch checked={!!enabled.economiser} onChange={(v) => setEnabled((e) => ({ ...e, economiser: v }))} label="Air-side economiser" />
            <p className="text-[11px] text-ink-3">Saves a new configuration version, then runs S0, S1 and S8.</p>
            {(rerun.error || patch.error) && <Callout level="error">{String((rerun.error ?? patch.error)?.message)}</Callout>}
            <button className="btn btn-primary self-start" disabled={rerun.isPending || (!!jobQ.data && ["queued", "running"].includes(jobQ.data.status))} onClick={() => rerun.mutate()}>Save &amp; re-run</button>
            {job && <JobBar job={jobQ.data} label="Re-run" />}
          </div>
          {rec && (
            <div className="card p-4 text-[12px]">
              <div className="font-semibold text-[13px] mb-1">Ranking (simulation-based)</div>
              <ol className="list-decimal ml-4">
                {rec.ranking.map((k) => <li key={k}>{k} — score {fmt(rec.candidates[k].score, 2)}, {fmt(rec.candidates[k].energy_saved_mwh)} MWh/yr</li>)}
              </ol>
              <p className="text-ink-3 mt-2">{rec.method}</p>
            </div>
          )}
        </div>
      </div>
      {results && (
        <div className="grid xl:grid-cols-2 gap-4">
          <ChartCard id="c13-airflow" number={13} title={`Required versus natural airflow${stackRun ? ` — ${stackRun.scenario_id}` : ""}`} units="kg/s (daily samples at the same hour)" period={period} source={`${src}; airflow network`}
            assumptions="Natural flow from the zonal network (stack + wind); where it is below server demand, fan assist is required (counted) or recirculation would occur."
            empty={!stackRun ? "No scenario with stack ventilation in this simulation." : nat.isLoading ? "Loading…" : undefined}
            option={nat.data ? lineChart(t, { time: true, zoom: true, yName: "kg/s", unit: "kg/s", series: [
              { name: "Natural chimney flow", data: (nat.data.columns.time_local as string[]).map((x, i) => [new Date(x.replace(" ", "T")).getTime(), nat.data!.columns.natural_flow_kg_s[i] as number]), color: t.series[0] },
              { name: "Server airflow required", data: (nat.data.columns.time_local as string[]).map((x, i) => [new Date(x.replace(" ", "T")).getTime(), nat.data!.columns.m_server_kg_s[i] as number]), color: t.series[1] },
            ] }) : undefined}
            table={nat.data ? { columns: ["time_local", "natural_flow_kg_s", "required_kg_s", "sufficient"], rows: (nat.data.columns.time_local as string[]).map((x, i) => [x, nat.data!.columns.natural_flow_kg_s[i], nat.data!.columns.m_server_kg_s[i], nat.data!.columns.natural_sufficient[i]]) } : undefined} />
          <ChartCard id="c17-soil-drift" number={17} title={`Soil-temperature drift over consecutive operating years${eaheRun ? ` — ${eaheRun.scenario_id}` : ""}`} units="K (mean disturbance at the pipe wall)" period={`${eaheRun?.summary.eahe?.yearly?.length ?? 0} repeated climate years`} source={`${src}; radial FD soil model`}
            assumptions="Each year repeats the climate record; soil properties as configured (default soil is an assumption)."
            empty={!eaheRun ? "No scenario with an earth-air heat exchanger in this simulation." : undefined}
            option={eaheRun ? barChart(t, { categories: eaheRun.summary.eahe.yearly.map((y: any) => `Year ${y.year}`), yName: "K", unit: "K", series: [
              { name: "mean drift", data: eaheRun.summary.eahe.yearly.map((y: any) => y.mean_drift_k), color: t.series[0] },
              { name: "max drift", data: eaheRun.summary.eahe.yearly.map((y: any) => y.max_drift_k), color: t.series[1] },
            ] }) : undefined}
            table={eaheRun ? { columns: ["year", "mean_drift_k", "max_drift_k", "sensible_cooling_mwh", "condensate_m3"], rows: eaheRun.summary.eahe.yearly.map((y: any) => [y.year, y.mean_drift_k, y.max_drift_k, y.sensible_cooling_kwh / 1000, y.condensate_m3]) } : undefined} />
          {eaheHourly.data && (
            <ChartCard id="c-eahe-temps" title={`Earth-air exchanger temperatures — ${eaheRun?.scenario_id}`} units="°C (hourly)" period={period} source={src} className="xl:col-span-2"
              assumptions="Outlet shown only for hours when the controller drew air through the exchanger; gaps are hours in other modes."
              option={lineChart(t, { time: true, zoom: true, yName: "°C", unit: "°C", series: [
                { name: "Outdoor air", data: (eaheHourly.data.columns.time_local as string[]).map((x, i) => [new Date(x.replace(" ", "T")).getTime(), eaheHourly.data!.columns.t_oa_c[i] as number]) },
                { name: "Undisturbed ground", data: (eaheHourly.data.columns.time_local as string[]).map((x, i) => [new Date(x.replace(" ", "T")).getTime(), eaheHourly.data!.columns.t_ground_c[i] as number]) },
                { name: "Exchanger outlet (when operating)", data: (eaheHourly.data.columns.time_local as string[]).map((x, i) => [new Date(x.replace(" ", "T")).getTime(), (eaheHourly.data!.columns.eahe_flow_m3s[i] as number) > 0 ? eaheHourly.data!.columns.eahe_t_out_c[i] as number : null]) },
              ] })}
              table={{ columns: ["time_local", "t_oa_c", "t_ground_c", "eahe_t_out_c", "eahe_flow_m3s", "soil_drift_k"], rows: (eaheHourly.data.columns.time_local as string[]).map((x, i) => [x, eaheHourly.data!.columns.t_oa_c[i], eaheHourly.data!.columns.t_ground_c[i], eaheHourly.data!.columns.eahe_t_out_c[i], eaheHourly.data!.columns.eahe_flow_m3s[i], eaheHourly.data!.columns.eahe_soil_drift_k[i]]) }} />
          )}
        </div>
      )}
      {!lib.data.strategies.length && <Empty title="Strategy library unavailable" />}
    </div>
  );
}
