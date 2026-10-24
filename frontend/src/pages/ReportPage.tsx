import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useJob } from "../api/hooks";
import { Callout, Empty, JobBar, Section, SourceBadge } from "../components/ui";
import { fmt, humanKey, pct, SCENARIO_SHORT, showValue } from "../lib/format";
import { bestScenario, useProjectPage } from "../lib/project";

const FORMATS: { id: string; label: string; note: string }[] = [
  { id: "pdf", label: "PDF report", note: "Structured engineering report with charts, equations, assumptions and references" },
  { id: "xlsx", label: "Excel workbook", note: "14 sheets: inputs, provenance, monthly/annual results, comparisons, formulas" },
  { id: "json", label: "JSON", note: "Complete machine-readable results, configuration and provenance" },
  { id: "zip", label: "Bundle (ZIP)", note: "PDF + Excel + JSON + hourly CSV for every scenario" },
];

interface ReportRow { id: string; format: string; status: string; job_id: string | null; sha256: string | null; created_at: string; download_url: string | null }

export function ReportPage() {
  const { project, results, simId, sim } = useProjectPage();
  const qc = useQueryClient();
  const [job, setJob] = useState<string | null>(null);
  const jobQ = useJob(job);
  const list = useQuery({ queryKey: ["reports", simId], queryFn: () => api.get<ReportRow[]>(`/api/simulations/${simId}/reports`), enabled: !!simId });
  const info = useQuery({ queryKey: ["model-info"], queryFn: () => api.get<{ model_version: string; limitations: string[]; references: string[] }>("/api/model-info"), staleTime: Infinity });
  useEffect(() => {
    if (jobQ.data && ["succeeded", "failed"].includes(jobQ.data.status)) qc.invalidateQueries({ queryKey: ["reports", simId] });
  }, [jobQ.data?.status, jobQ.data, qc, simId]);
  const create = useMutation({
    mutationFn: (format: string) => api.post<{ report_id: string; job_id: string }>("/api/reports", { simulation_id: simId, format }),
    onSuccess: (r) => setJob(r.job_id),
  });
  const reproduce = useMutation({ mutationFn: (sid: string) => api.post<any>(`/api/simulations/${simId}/reproduce?scenario_id=${sid}`) });

  if (sim.isLoading || project.isLoading) return <p className="text-ink-2">Loading…</p>;
  if (!results || !project.data) {
    return <Empty title="No completed simulation">The report is generated from stored simulation results. Run the scenarios on the Setup page first.</Empty>;
  }
  const p = project.data;
  const cfg = p.config;
  const rows = results.comparison ?? {};
  const best = bestScenario(rows);
  const b = best ? rows[best] : undefined;
  const rec = results.recommendation;
  const ds = p.datasets.find((d) => d.id === results.dataset_id);
  const cs = results.climate_summary;
  const assumed = p.assumptions.filter((a) => a.source === "default-assumption");
  const flags = Object.values(rows).flatMap((r) => r.flags.filter((f) => f.level !== "info").map((f) => ({ ...f, scenario: r.id })));
  const gaps = Array.from(new Set(Object.values(rows).flatMap((r) => [...(r.lifecycle?.capex_data_gaps ?? []).map((g: string) => `capital cost: ${g}`), ...((r.lifecycle?.embodied?.data_gaps ?? []) as string[]).map((g) => `embodied-carbon factor: ${g}`)])));
  if (rows.S0 && rows.S0.metrics.co2e_t == null) gaps.unshift("grid emission factor (operational carbon not calculated)");
  if (cfg.economics.electricity_price_per_kwh == null) gaps.push("electricity tariff (operating cost not calculated)");
  const running = jobQ.data && ["queued", "running"].includes(jobQ.data.status);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-[20px] font-semibold">Design report</h1>
        <p className="text-[12px] text-ink-2 max-w-4xl">
          Exports are generated from the stored results of simulation {simId?.slice(0, 8)} (model {results.model_version}, configuration hash {results.config_hash.slice(0, 12)},
          climate dataset SHA-256 {results.dataset_sha256?.slice(0, 12)}). Every file carries the same provenance and the same numbers as this page.
        </p>
      </div>

      <Section title="Export">
        <div className="grid md:grid-cols-2 xl:grid-cols-4 gap-3">
          {FORMATS.map((f) => (
            <div key={f.id} className="card p-4 flex flex-col gap-2">
              <div className="font-semibold text-[14px]">{f.label}</div>
              <p className="text-[12px] text-ink-2 flex-1">{f.note}</p>
              <button className="btn btn-primary self-start" disabled={!!running || create.isPending} onClick={() => create.mutate(f.id)}>Generate</button>
            </div>
          ))}
        </div>
        {create.error && <Callout level="error">{String(create.error.message)}</Callout>}
        {jobQ.data && <JobBar job={jobQ.data} label="Report generation" />}
        <div className="card p-3 text-[12px]">
          <div className="font-semibold mb-1">Generated files</div>
          {!list.data?.length ? <p className="text-ink-3">None yet for this simulation.</p> : (
            <table className="w-full tnum">
              <thead><tr className="border-b border-line text-left"><th className="py-1">Created (UTC)</th><th>Format</th><th>Status</th><th>SHA-256</th><th /></tr></thead>
              <tbody>
                {list.data.map((r) => (
                  <tr key={r.id} className="border-b border-line">
                    <td className="py-1">{r.created_at.slice(0, 19).replace("T", " ")}</td>
                    <td>{r.format}</td>
                    <td>{r.status}</td>
                    <td className="font-mono text-[11px]">{r.sha256?.slice(0, 16) ?? ""}</td>
                    <td className="text-right">{r.download_url && <a className="btn !py-0.5 !px-2" href={api.url(r.download_url)}>Download</a>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="flex flex-wrap gap-2 mt-2">
            <a className="btn !py-0.5 !px-2" href={api.url(`/api/simulations/${simId}/comparison.csv`)}>Comparison CSV</a>
            {results.scenarios.map((s) => <a key={s} className="btn !py-0.5 !px-2" href={api.url(`/api/simulations/${simId}/scenarios/${s}/hourly?format=csv`)}>{s} hourly CSV</a>)}
            <a className="btn !py-0.5 !px-2" href={api.url(`/api/climate/${results.dataset_id}/timeseries?format=csv`)}>Climate CSV</a>
          </div>
        </div>
      </Section>

      <Section title="Executive summary (preview)">
        <div className="card p-4 text-[13px] flex flex-col gap-2 leading-relaxed">
          <p>
            <b>{p.name}</b> — {cfg.site.name ?? "site"} ({fmt(cfg.site.latitude, 3)}, {fmt(cfg.site.longitude, 3)}, {fmt(cfg.site.elevation_m)} m),{" "}
            {fmt(cfg.it.installed_capacity_mw, 1)} MW installed IT at {fmt(cfg.it.average_utilisation * 100)} % mean utilisation, ASHRAE class {cfg.it.ashrae_class} ({cfg.it.envelope_mode} envelope).
          </p>
          {rows.S0 && b && (
            <p>
              The conventional reference (S0) has an annual PUE of <b>{fmt(rows.S0.metrics.pue, 3)}</b> and uses {fmt(rows.S0.metrics.facility_mwh)} MWh/yr and{" "}
              {fmt(rows.S0.metrics.water_withdrawal_m3)} m³/yr of water. The best-scoring scenario is <b>{best} ({b.label})</b> with PUE <b>{fmt(b.metrics.pue, 3)}</b>,{" "}
              {fmt(b.metrics.facility_mwh)} MWh/yr ({pct(b.delta_pct.facility_mwh)}), cooling electricity {pct(b.delta_pct.cooling_mwh)}, water {fmt(b.metrics.water_withdrawal_m3)} m³/yr ({pct(b.delta_pct.water_withdrawal_m3)})
              {b.metrics.co2e_t != null ? `, operational CO₂e ${pct(b.delta_pct.co2e_t)}` : ""}; unmet-load hours {fmt(b.metrics.unmet_hours)}.
            </p>
          )}
          {rec && (
            <p>
              Strategies ranked for further study (each simulated on S1): {rec.ranking.slice(0, 4).map((k) => `${k} (${fmt(rec.candidates[k].energy_saved_mwh)} MWh/yr)`).join(", ") || "none"}.
              {Object.values(rec.candidates).some((c: any) => !c.feasible) && <> Excluded as infeasible: {Object.values(rec.candidates).filter((c: any) => !c.feasible).map((c: any) => c.candidate).join(", ")}.</>}
            </p>
          )}
          <p className="text-[12px] text-ink-2">
            Climate: {ds ? `${ds.provider} / ${ds.product} (${ds.data_nature})` : "dataset"}, {cs?.period.start_utc.slice(0, 10)} – {cs?.period.end_utc.slice(0, 10)}
            {ds?.distance_km != null ? `, source ${fmt(ds.distance_km, 1)} km from the site` : ""}. Results are screening-level and not validated for construction.
          </p>
        </div>
      </Section>

      {rec && (
        <Section title="Recommended architectural interventions">
          <div className="grid lg:grid-cols-2 gap-3">
            {rec.ranking.map((k) => {
              const c = rec.candidates[k];
              const e = c.explanation ?? {};
              return (
                <article key={k} className="card p-4 text-[12px] flex flex-col gap-1.5">
                  <div className="flex justify-between gap-2"><h3 className="font-semibold text-[14px]">{k}</h3><span className="text-ink-2">{e.verdict}</span></div>
                  {e.annual_benefit && <p><span className="text-ink-2">Annual benefit:</span> {e.annual_benefit}</p>}
                  {e.peak_benefit && <p><span className="text-ink-2">Peak:</span> {e.peak_benefit}</p>}
                  {e.why_suitable?.length > 0 && <p><span className="text-ink-2">Why here:</span> {e.why_suitable.join(" ")}</p>}
                  {e.climate_evidence?.length > 0 && <p><span className="text-ink-2">Climate evidence:</span> {e.climate_evidence.join(" ")}</p>}
                  {e.mechanism && <p><span className="text-ink-2">Mechanism:</span> {e.mechanism}</p>}
                  {e.geometry && Object.keys(e.geometry).length > 0 && <p><span className="text-ink-2">Geometry / sizing:</span> {Object.entries(e.geometry).map(([g, v]) => `${humanKey(g)} ${showValue(v)}`).join("; ")}</p>}
                  {e.auxiliary && <p><span className="text-ink-2">Auxiliary energy:</span> {e.auxiliary}</p>}
                  {e.disadvantages?.length > 0 && <p><span className="text-ink-2">Disadvantages / risks:</span> {e.disadvantages.join("; ")}</p>}
                  {e.uncertainty && <p><span className="text-ink-2">Uncertainty:</span> {e.uncertainty}</p>}
                  {e.required_studies?.length > 0 && <p><span className="text-ink-2">Required before construction:</span> {e.required_studies.join("; ")}</p>}
                </article>
              );
            })}
          </div>
        </Section>
      )}

      <div className="grid xl:grid-cols-2 gap-4">
        <Section title="Data gaps">
          <div className="card p-4 text-[12px] flex flex-col gap-2">
            {gaps.length ? <ul className="list-disc ml-4">{gaps.map((g) => <li key={g}>{g}</li>)}</ul> : <p className="text-ink-3">No missing economic or carbon inputs.</p>}
            <div className="font-semibold mt-1">Values still on default assumptions ({assumed.length})</div>
            <div className="max-h-[260px] overflow-auto">
              <table className="w-full">
                <tbody>
                  {assumed.map((a) => (
                    <tr key={a.path} className="border-b border-line align-top">
                      <td className="py-0.5 pr-2 font-mono text-[11px]">{a.path}</td>
                      <td className="pr-2 tnum">{typeof a.value === "object" ? JSON.stringify(a.value) : String(a.value)}</td>
                      <td><SourceBadge source={a.source} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </Section>
        <Section title="Model flags">
          <div className="card p-4 text-[12px] flex flex-col gap-1.5 max-h-[360px] overflow-auto">
            {!flags.length ? <p className="text-ink-3">No warnings in any scenario.</p> : flags.map((f, i) => (
              <Callout key={i} level={f.level}>{f.scenario} {SCENARIO_SHORT[f.scenario] ?? ""}: {f.message}</Callout>
            ))}
          </div>
        </Section>
      </div>

      <Section title="Reproducibility check">
        <div className="card p-4 text-[12px] flex flex-col gap-2">
          <p className="text-ink-2">Re-runs a stored scenario from its saved configuration and climate dataset and compares the input hash and the hourly facility power.</p>
          <div className="flex gap-2 flex-wrap">
            {results.scenarios.map((s) => <button key={s} className="btn" disabled={reproduce.isPending} onClick={() => reproduce.mutate(s)}>Reproduce {s}</button>)}
          </div>
          {reproduce.isPending && <p className="text-ink-3">Re-running…</p>}
          {reproduce.error && <Callout level="error">{String(reproduce.error.message)}</Callout>}
          {reproduce.data && (
            <Callout level={reproduce.data.reproduced ? "good" : "warning"} title={reproduce.data.reproduced ? "Reproduced" : "Not identical"}>
              {reproduce.data.scenario_id}: inputs hash {reproduce.data.inputs_hash_stored.slice(0, 12)} → {reproduce.data.inputs_hash_now.slice(0, 12)}; model {reproduce.data.model_version_stored} → {reproduce.data.model_version_now};
              facility {fmt(reproduce.data.facility_mwh_stored, 3)} → {fmt(reproduce.data.facility_mwh_now, 3)} MWh; hourly series {reproduce.data.hourly_identical ? "identical" : "differ"}.
            </Callout>
          )}
        </div>
      </Section>

      {info.data && (
        <div className="grid xl:grid-cols-2 gap-4">
          <Section title="Limitations">
            <ul className="card p-4 text-[12px] list-disc pl-8 flex flex-col gap-1">{info.data.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
          </Section>
          <Section title="References">
            <ol className="card p-4 text-[12px] list-decimal pl-8 flex flex-col gap-1">{info.data.references.map((l) => <li key={l}>{l}</li>)}</ol>
          </Section>
        </div>
      )}
    </div>
  );
}
