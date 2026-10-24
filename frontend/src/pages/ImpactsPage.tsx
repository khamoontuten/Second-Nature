import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useJob, usePatchProject } from "../api/hooks";
import { ChartCard } from "../charts/ChartCard";
import { barChart, heatmap, type Tokens } from "../charts/options";
import { useTokens } from "../charts/useTokens";
import { byKey, DeltaText, fmtPct, fmtVal, StatusBadge, StatusLegend } from "../components/impact";
import { Callout, Empty, Field, JobBar, Section, StatTile, Switch } from "../components/ui";
import { useProjectPage } from "../lib/project";
import type { CatalogueStrategy, ImpactCatalogue, ImpactEval, ImpactMetric, ImpactStudy, PairResult } from "../types/api";

const ARCHITECTURES = ["air_cooled_chiller_crah", "water_cooled_chiller_tower", "dx_crac", "dx_with_airside_economiser", "direct_evaporative",
  "indirect_evaporative", "liquid_cooling_dry_cooler"];

const COLUMNS: [string, string][] = [["facility_mwh", "Facility electricity"], ["cooling_mwh", "Cooling electricity"], ["pue", "PUE"],
  ["water_withdrawal_m3", "Water withdrawal"], ["co2e_t", "Operational CO₂e"], ["capex", "CAPEX"], ["site_at_grade_m2", "Footprint at grade"],
  ["underground_m2", "Underground"]];

const CHART_METRICS: { key: string; label: string; mode: "pct" | "abs" }[] = [
  { key: "facility_mwh", label: "Facility electricity", mode: "pct" }, { key: "cooling_mwh", label: "Cooling electricity", mode: "pct" },
  { key: "water_withdrawal_m3", label: "Water withdrawal", mode: "abs" }, { key: "co2e_t", label: "Operational CO₂e", mode: "pct" },
  { key: "capex", label: "CAPEX", mode: "pct" }, { key: "site_at_grade_m2", label: "Footprint at grade", mode: "abs" },
  { key: "underground_m2", label: "Underground area", mode: "abs" },
];

const INTERACTION_KEYS: [string, string][] = [["facility_mwh", "Facility electricity"], ["cooling_mwh", "Cooling electricity"],
  ["water_withdrawal_m3", "Water withdrawal"], ["co2e_t", "Operational CO₂e"], ["capex", "CAPEX"], ["site_at_grade_m2", "Footprint at grade"]];
const SHORT: Record<string, string> = {
  containment_full: "Containment", economiser_direct: "Direct econ.", economiser_indirect: "Indirect econ.", stack: "Stack", solar_chimney: "Solar chimney",
  envelope: "Envelope", facade_fixed: "Fixed shading", facade_kinetic: "Kinetic facade", eahe: "Earth-air HX", storage: "Storage", radiative: "Radiative",
  iec: "Indirect evap.", evap_direct: "Direct evap.", evap_precool: "Evap. pre-cool",
};
const CLASS_SHORT: Record<string, string> = { additive: "A", complementary: "C", overlapping: "O", conflicting: "X", "not evaluated": "–" };

function useStudy(id?: string) {
  return useQuery({ queryKey: ["impact-study", id], queryFn: () => api.get<ImpactStudy>(`/api/impact-studies/${id}`), enabled: !!id, staleTime: Infinity });
}

/** Client-side preview of dependency resolution and exclusions (the server re-checks before simulating). */
function resolveSelection(sel: string[], cat: CatalogueStrategy[]) {
  const by = Object.fromEntries(cat.map((c) => [c.id, c]));
  const chosen = new Set(sel);
  const added: string[] = [];
  let changed = true;
  while (changed) {
    changed = false;
    for (const id of Array.from(chosen)) for (const r of by[id]?.requires ?? []) if (!chosen.has(r)) { chosen.add(r); added.push(r); changed = true; }
  }
  const groups: Record<string, string[]> = {};
  for (const id of chosen) { const g = by[id]?.exclusive_group; if (g) (groups[g] ??= []).push(id); }
  const conflicts = Object.entries(groups).filter(([, ids]) => ids.length > 1).map(([g, ids]) => `${ids.map((i) => by[i].name).join(" and ")} are alternative ${g} options and cannot be combined.`);
  return { resolved: cat.filter((c) => chosen.has(c.id)).map((c) => c.id), added, conflicts };
}

export function ImpactsPage() {
  const { projectId, project } = useProjectPage();
  const t = useTokens();
  const qc = useQueryClient();
  const cat = useQuery({ queryKey: ["impact-catalogue", projectId, project.data?.current_version_no],
    queryFn: () => api.get<ImpactCatalogue>("/api/impacts/catalogue", { project_id: projectId }), enabled: !!project.data });
  const baseline = useQuery({ queryKey: ["impact-baseline", projectId, project.data?.current_version_no],
    queryFn: () => api.get<any>(`/api/projects/${projectId}/baseline`), enabled: !!project.data });
  const studies = useQuery({ queryKey: ["impact-studies", projectId], queryFn: () => api.get<ImpactStudy[]>(`/api/projects/${projectId}/impact-studies`) });
  const [picked, setPicked] = useState<Record<string, string | undefined>>({});
  const latest = (kind: string) => picked[kind] ?? studies.data?.find((s) => s.kind === kind && s.status === "succeeded")?.id;
  const ind = useStudy(latest("individual"));
  const combo = useStudy(latest("combination"));
  const pairs = useStudy(latest("pairwise"));
  const curV = project.data?.current_version_no;
  const ok = (studies.data ?? []).filter((s) => s.status === "succeeded");
  const anyStudy = useStudy((ok.find((s) => s.config_version_no === curV) ?? ok[0])?.id);
  const [jobs, setJobs] = useState<Record<string, string>>({});
  const jBase = useJob(jobs.baseline);
  const jInd = useJob(jobs.individual);
  const jCombo = useJob(jobs.combination);
  const jPair = useJob(jobs.pairwise);
  const doneKey = [jBase.data?.status, jInd.data?.status, jCombo.data?.status, jPair.data?.status].join();
  useEffect(() => {
    if (doneKey.includes("succeeded") || doneKey.includes("failed")) {
      qc.invalidateQueries({ queryKey: ["impact-studies", projectId] });
      setPicked({});
    }
  }, [doneKey, projectId, qc]);
  const run = useMutation({
    mutationFn: (body: { kind: string; strategies?: string[]; parameters?: Record<string, number> }) =>
      api.post<{ impact_study_id: string; job_id: string }>("/api/impact-studies", { project_id: projectId, ...body }),
    onSuccess: (r, vars) => setJobs((j) => ({ ...j, [vars.kind]: r.job_id })),
  });
  const busy = (j?: { status: string }) => !!j && ["queued", "running"].includes(j.status);

  // combination builder state (initialised from the last combination study)
  const [sel, setSel] = useState<string[] | null>(null);
  const [params, setParams] = useState<Record<string, number>>({});
  useEffect(() => {
    if (sel === null && studies.data) {
      const last = studies.data.find((s) => s.kind === "combination" && s.status === "succeeded");
      setSel(last?.request.strategies ?? []);
      setParams(last?.request.parameters ?? {});
    }
  }, [studies.data, sel]);

  if (!project.data || !cat.data) return <p className="text-ink-2">Loading…</p>;
  const catalogue = cat.data.strategies;
  const curVersion = project.data.current_version_no;
  const stale = (s?: ImpactStudy) => !!s && s.config_version_no !== curVersion;

  return (
    <div className="flex flex-col gap-7">
      <div className="flex flex-col gap-2">
        <h1 className="text-[20px] font-semibold">Strategy impact analysis</h1>
        <p className="text-[12px] text-ink-2 max-w-5xl">
          Each strategy is simulated alone against one conventional baseline, and any combination you select is simulated as a whole —
          combined results are never added up from individual savings. Cost and space come from quantity take-offs of the simulated
          configuration with the project's unit rates and layout assumptions, so shared infrastructure is counted once. Every value carries how it was obtained:
        </p>
        <StatusLegend />
      </div>

      <BaselineView projectId={projectId} baseline={baseline.data} baseEval={anyStudy.data?.result?.baseline} curVersion={curVersion}
        study={anyStudy.data} onRun={() => run.mutate({ kind: "individual" })} onRunBaseline={() => run.mutate({ kind: "baseline" })}
        running={busy(jInd.data) || busy(jBase.data) || run.isPending} />
      {run.error && <Callout level="error">{String(run.error.message)}</Callout>}
      {jobs.baseline && <JobBar job={jBase.data} label="Baseline simulation" />}
      {jobs.individual && <JobBar job={jInd.data} label="Individual impact study" />}

      <Section title="B · Individual strategy impacts" id="individual">
        {!ind.data?.result ? (
          <Empty title="No individual impact study yet">Run the individual study above: every applicable strategy is simulated alone against the baseline.</Empty>
        ) : (
          <>
            {stale(ind.data) && <Callout level="warning">These results use configuration v{ind.data.config_version_no}; the project is now at v{curVersion}. Re-run to update.</Callout>}
            <IndividualView study={ind.data} catalogue={catalogue} t={t} />
          </>
        )}
      </Section>

      <Section title="C · Combination builder" id="builder">
        <Builder catalogue={catalogue} applicability={cat.data.applicability} sel={sel ?? []} setSel={(s) => setSel(s)} params={params} setParams={setParams}
          sharedLines={cat.data.cost_lines.filter((c) => c.scope === "shared")}
          onRun={() => run.mutate({ kind: "combination", strategies: sel ?? [], parameters: params })}
          onPairs={() => run.mutate({ kind: "pairwise", strategies: sel ?? [], parameters: params })}
          running={busy(jCombo.data) || run.isPending} pairRunning={busy(jPair.data)} />
        {jobs.combination && <JobBar job={jCombo.data} label="Combination study" />}
        {jobs.pairwise && <JobBar job={jPair.data} label="Pairwise interaction study" />}
      </Section>

      <Section title="D · Combination results versus baseline" id="results" right={
        <StudyPicker kind="combination" studies={studies.data} value={latest("combination")} onChange={(id) => setPicked((p) => ({ ...p, combination: id }))} />}>
        {!combo.data?.result?.combination ? (
          <Empty title="No combination evaluated yet">Select strategies in the builder and run the combination.</Empty>
        ) : (
          <>
            {stale(combo.data) && <Callout level="warning">These results use configuration v{combo.data.config_version_no}; the project is now at v{curVersion}. Re-run to update.</Callout>}
            <ResultsView ev={combo.data.result.combination} base={combo.data.result.baseline} study={combo.data} t={t} />
          </>
        )}
      </Section>

      <Section title="E · Strategy attribution and interactions" id="attribution">
        {combo.data?.result?.attribution ? <AttributionView study={combo.data} catalogue={catalogue} t={t} /> :
          <Empty title="No combination to attribute">Attribution compares the combined result with each selected strategy evaluated alone.</Empty>}
        <div className="flex items-end justify-between gap-2 mt-2">
          <h3 className="text-[14px] font-semibold">Pairwise interaction matrix</h3>
          <StudyPicker kind="pairwise" studies={studies.data} value={latest("pairwise")} onChange={(id) => setPicked((p) => ({ ...p, pairwise: id }))} />
        </div>
        {pairs.data?.result?.pairs ? <PairwiseView study={pairs.data} catalogue={catalogue} t={t} /> :
          <Empty title="No pairwise study yet">Select 2–10 strategies in the builder and run “Pairwise interactions”; every pair is simulated.</Empty>}
      </Section>

      <Section title="F · Export" id="export">
        <ExportView studies={studies.data ?? []} />
      </Section>
    </div>
  );
}

// ------------------------------------------------------------------------------------------- A · baseline
function BaselineView({ projectId, baseline, baseEval, curVersion, study, onRun, onRunBaseline, running }: {
  projectId: string; baseline?: any; baseEval?: ImpactEval; curVersion: number; study?: ImpactStudy; onRun: () => void; onRunBaseline: () => void; running: boolean;
}) {
  const patch = usePatchProject(projectId);
  const qc = useQueryClient();
  const [draft, setDraft] = useState<Record<string, any> | null>(null);
  useEffect(() => {
    if (baseline) setDraft({ cap: baseline.it.installed_capacity_mw, util: baseline.it.average_utilisation, arch: baseline.cooling.architecture,
      cop: baseline.cooling.rated_cop, ef: baseline.grid.emission_factor_kg_per_kwh, price: baseline.economics.electricity_price_per_kwh });
  }, [baseline]);
  if (!baseline || !draft) return <Section title="A · Baseline configuration"><p className="text-ink-2">Loading…</p></Section>;
  const m = byKey(baseEval?.metrics);
  const save = () => patch.mutate({
    patch: { it: { installed_capacity_mw: Number(draft.cap), average_utilisation: Number(draft.util) },
      baseline: { architecture: draft.arch, rated_cop: draft.cop === "" || draft.cop === null ? null : Number(draft.cop) },
      grid: { emission_factor_kg_per_kwh: draft.ef === "" || draft.ef === null ? null : Number(draft.ef) },
      economics: { electricity_price_per_kwh: draft.price === "" || draft.price === null ? null : Number(draft.price) } },
    note: "baseline edited on the impact-analysis page",
  }, { onSuccess: () => qc.invalidateQueries({ queryKey: ["impact-baseline", projectId] }) });
  const capex = m.capex;
  const ds = baseline.dataset;
  return (
    <Section title="A · Baseline configuration" id="baseline" right={
      <div className="flex gap-2">
        <button className="btn" disabled={running} onClick={onRunBaseline}>Run baseline</button>
        <button className="btn btn-primary" disabled={running} onClick={onRun}>Run individual impact study</button>
      </div>}>
      <div className="grid xl:grid-cols-[1.3fr_1fr] gap-4">
        <div className="card p-4 text-[12.5px] grid md:grid-cols-2 gap-x-6 gap-y-1.5 tnum">
          <p className="md:col-span-2 text-ink-2 mb-1">{baseline.description} Configuration v{curVersion}; {baseline.assumed_count} of {baseline.assumptions.length} baseline inputs are default assumptions.</p>
          <Kv k="Site" v={`${baseline.site.name} (${baseline.site.latitude.toFixed(3)}, ${baseline.site.longitude.toFixed(3)}, ${Math.round(baseline.site.elevation_m)} m)`} />
          <Kv k="Weather" v={ds ? `${ds.product} (${ds.data_nature}), ${String(ds.start_utc).slice(0, 10)} – ${String(ds.end_utc).slice(0, 10)}, ${ds.n_hours} h` : "no dataset"} />
          <Kv k="IT load" v={`${baseline.it.installed_capacity_mw} MW installed, mean ${baseline.it.mean_it_load_mw.toFixed(2)} MW (${baseline.it.utilisation_scenario})`} />
          <Kv k="Schedule" v={baseline.it.schedule} />
          <Kv k="Inlet requirement" v={`ASHRAE ${baseline.it.ashrae_class} ${baseline.it.envelope_mode}, setpoint ${baseline.it.inlet_setpoint_c} °C, ΔT ${baseline.it.server_delta_t_k} K`} />
          <Kv k="Cooling" v={`${baseline.cooling.architecture.replace(/_/g, " ")}, rated COP ${baseline.cooling.rated_cop ?? "default"}, CHW ${baseline.cooling.chw_supply_c} °C, ${baseline.cooling.redundancy}`} />
          <Kv k="Containment" v={baseline.it.containment} />
          <Kv k="Grid factor" v={baseline.grid.emission_factor_kg_per_kwh == null ? "not supplied (carbon not calculated)" : `${baseline.grid.emission_factor_kg_per_kwh} kg/kWh (${baseline.grid.factor_type}; ${baseline.grid.source})`} />
          <Kv k="Tariffs" v={`${baseline.economics.electricity_price_per_kwh ?? "—"} ${baseline.economics.currency}/kWh; water ${baseline.economics.water_price_per_m3 ?? "—"} ${baseline.economics.currency}/m³`} />
          <Kv k="Unit rates" v={`${baseline.economics.unit_rates_supplied} supplied; contingency ${(baseline.economics.contingency_fraction * 100).toFixed(0)} %`} />
          <Kv k="Building" v={`${baseline.building.length_m} × ${baseline.building.width_m} m, ${baseline.building.floors} floor(s); site ${baseline.building.site_area_m2 ?? "—"} m²`} />
          <div className="md:col-span-2 grid grid-cols-2 md:grid-cols-4 gap-2 mt-2">
            <StatTile label="Baseline facility electricity" value={fmtVal(m.facility_mwh?.value, "MWh")} unit="MWh/yr" sub={m.pue ? `PUE ${fmtVal(m.pue.value, "–")}` : "run a study"} />
            <StatTile label="Baseline water withdrawal" value={fmtVal(m.water_withdrawal_m3?.value, "m³")} unit="m³/yr" />
            <StatTile label="Baseline CAPEX" value={capex?.value != null ? fmtVal(capex.value, capex.unit) : "—"} unit={capex?.value != null ? capex.unit : undefined}
              sub={capex ? (capex.value != null ? `range ${fmtVal(capex.range?.[0], capex.unit)} – ${fmtVal(capex.range?.[1], capex.unit)}` : capex.note) : "run a study"} />
            <StatTile label="Baseline footprint at grade" value={fmtVal(m.site_at_grade_m2?.value, "m²")} unit="m²"
              sub={m.site_at_grade_m2 ? `building ${fmtVal(m.building_footprint_m2?.value, "m²")} m² + yard ${fmtVal(m.outdoor_yard_m2?.value, "m²")} m²` : "run a study"} />
          </div>
          {study && <p className="md:col-span-2 text-[11px] text-ink-3 mt-1" data-testid="baseline-source">Baseline values above are from the {study.kind} study of {study.created_at.slice(0, 16).replace("T", " ")} UTC (configuration v{study.config_version_no}{study.config_version_no !== curVersion ? " — re-run for the current version" : ""}).</p>}
        </div>
        <div className="card p-4 flex flex-col gap-2">
          <div className="font-semibold text-[13px]">Edit key baseline inputs</div>
          <p className="text-[11px] text-ink-3">Saving creates a new configuration version; re-run studies afterwards. All other inputs are on the Setup page.</p>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Installed IT" unit="MW"><input className="input tnum" type="number" step="0.5" min={0.1} value={draft.cap} onChange={(e) => setDraft({ ...draft, cap: e.target.value })} /></Field>
            <Field label="Mean utilisation" unit="–"><input className="input tnum" type="number" step="0.05" min={0.05} max={1} value={draft.util} onChange={(e) => setDraft({ ...draft, util: e.target.value })} /></Field>
            <Field label="Cooling architecture">
              <select className="input" value={draft.arch} onChange={(e) => setDraft({ ...draft, arch: e.target.value })}>{ARCHITECTURES.map((a) => <option key={a} value={a}>{a.replace(/_/g, " ")}</option>)}</select>
            </Field>
            <Field label="Rated COP" unit="blank = default"><input className="input tnum" type="number" step="0.1" min={1} value={draft.cop ?? ""} onChange={(e) => setDraft({ ...draft, cop: e.target.value })} /></Field>
            <Field label="Grid factor" unit="kg/kWh"><input className="input tnum" type="number" step="0.01" min={0} value={draft.ef ?? ""} onChange={(e) => setDraft({ ...draft, ef: e.target.value })} /></Field>
            <Field label="Electricity price" unit={`${baseline.economics.currency}/kWh`}><input className="input tnum" type="number" step="0.01" min={0} value={draft.price ?? ""} onChange={(e) => setDraft({ ...draft, price: e.target.value })} /></Field>
          </div>
          {patch.error && <Callout level="error">{String(patch.error.message)}</Callout>}
          <div className="flex gap-2 items-center">
            <button className="btn" disabled={patch.isPending} onClick={save}>Save baseline changes</button>
            <a className="text-[12px] underline text-ink-2" href={`/projects/${projectId}/setup`}>All inputs (Setup)</a>
          </div>
        </div>
      </div>
    </Section>
  );
}

const Kv = ({ k, v }: { k: string; v: string }) => (
  <div className="flex gap-2 min-w-0"><span className="text-ink-2 w-[118px] shrink-0">{k}</span><span className="min-w-0">{v}</span></div>
);

// ------------------------------------------------------------------------------------------- B · individual
function IndividualView({ study, catalogue, t }: { study: ImpactStudy; catalogue: CatalogueStrategy[]; t: Tokens }) {
  const res = study.result!;
  const [metric, setMetric] = useState(CHART_METRICS[0]);
  const [open, setOpen] = useState<string | null>(null);
  const by = Object.fromEntries(catalogue.map((c) => [c.id, c]));
  const evs = res.individual;
  const vals = evs.map((e) => {
    const m = byKey(e.metrics)[metric.key];
    return m ? (metric.mode === "pct" ? m.pct : m.delta) : null;
  });
  const unit = metric.mode === "pct" ? "%" : byKey(res.baseline.metrics)[metric.key]?.unit ?? "";
  const notApplicable = Object.entries(res.applicability).filter(([, a]) => !a.applicable);
  const period = `${res.period.start_utc.slice(0, 10)} – ${res.period.end_utc.slice(0, 10)} (${res.period.hours} h)`;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-[12px] text-ink-2">
        {evs.length} strategies, each simulated alone ({res.simulations_run} hourly simulations, model {res.model_version}). Changes are relative to the baseline;
        declared dependencies (e.g. full containment for stack ventilation) are added automatically and shown. {res.controller_note}
      </p>
      {notApplicable.length > 0 && <Callout level="info" title="Not applicable to this baseline">{notApplicable.map(([id, a]) => `${by[id]?.name ?? id}: ${a.reasons.join("; ")}`).join(" · ")}</Callout>}
      <ChartCard id="impact-individual" title={`Individual impact — ${metric.label}`} units={metric.mode === "pct" ? "% change vs baseline" : `change vs baseline (${unit})`}
        period={period} source={`Second Nature impact study ${study.id.slice(0, 8)}, configuration v${study.config_version_no}`}
        assumptions={metric.key === "capex" ? "CAPEX from the project's unit rates (see Assumptions); ranges in the table." :
          metric.mode === "abs" ? "Absolute change shown (a percentage of a small or zero baseline would mislead)." : "Negative = reduction relative to the baseline."}
        height={Math.max(220, evs.length * 26 + 60)}
        empty={vals.every((v) => v === null) ? "No values for this metric (insufficient data or infeasible) — see the table." : undefined}
        option={barChart(t, { categories: evs.map((e) => by[e.requested[0]]?.name ?? e.label), horizontal: true, labels: true, yName: unit, unit,
          series: [{ name: metric.label, data: vals, colors: vals.map((v) => (v === null || v === 0 ? t.neutral : v < 0 ? t.divNeg : t.divPos)) }] })}
        table={{ columns: ["strategy", "feasible", `${metric.key} change (${unit})`, "status"], rows: evs.map((e, i) => [e.requested[0], e.feasible, vals[i], byKey(e.metrics)[metric.key]?.status]) }} />
      <div className="flex gap-2 flex-wrap text-[12px] -mt-1" role="group" aria-label="Metric shown in the chart">
        {CHART_METRICS.map((c) => <button key={c.key} className={`btn !py-1 !px-2 ${c.key === metric.key ? "btn-primary" : ""}`} aria-pressed={c.key === metric.key} onClick={() => setMetric(c)}>{c.label}</button>)}
      </div>
      <div className="card overflow-auto">
        <table className="w-full text-[12px]" data-testid="individual-table">
          <thead>
            <tr className="border-b border-line text-left">
              <th className="px-2 py-1.5 font-semibold">Strategy</th>
              {COLUMNS.map(([, l]) => <th key={l} className="px-2 py-1.5 font-semibold">{l}</th>)}
              <th className="px-2 py-1.5 font-semibold">Feasibility</th>
            </tr>
          </thead>
          <tbody>
            {evs.map((e) => {
              const m = byKey(e.metrics);
              const c = by[e.requested[0]];
              return (
                <Fragment key={e.id}>
                  <tr className="border-b border-line align-top" data-strategy={e.requested[0]}>
                    <td className="px-2 py-1.5 min-w-[220px]">
                      <button className="text-left font-medium underline-offset-2 hover:underline" aria-expanded={open === e.id} onClick={() => setOpen(open === e.id ? null : e.id)}>
                        {open === e.id ? "▾" : "▸"} {c?.name ?? e.label}
                      </button>
                      <div className="text-[11px] text-ink-3">{c?.category}{e.auto_added.length ? ` · adds ${e.auto_added.join(", ")}` : ""}</div>
                    </td>
                    {COLUMNS.map(([k]) => <td key={k} className="px-2 py-1.5" data-metric={k}><DeltaText m={m[k]} showStatus={m[k]?.status !== "modelled" && m[k]?.status !== "derived"} /></td>)}
                    <td className="px-2 py-1.5">{e.feasible ? <span>✓ feasible</span> : <span title={e.infeasible_reasons.join("; ")}><StatusBadge status="infeasible" /></span>}</td>
                  </tr>
                  {open === e.id && (
                    <tr className="border-b border-line"><td colSpan={COLUMNS.length + 2} className="p-3 bg-[var(--surface-2)]"><EvalDetail ev={e} strategy={c} /></td></tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-ink-3">Arrows show direction (▼ lower, ▲ higher); the colour of the arrow marks improvement or trade-off and is repeated in words for screen readers. Hover a value for its basis.</p>
    </div>
  );
}

function EvalDetail({ ev, strategy }: { ev: ImpactEval; strategy?: CatalogueStrategy }) {
  return (
    <div className="grid xl:grid-cols-[1fr_1.4fr] gap-4 text-[12px]">
      <div className="flex flex-col gap-1.5">
        {strategy && (
          <>
            <div><span className="text-ink-2">Inspiration: </span>{strategy.inspiration}</div>
            <div><span className="text-ink-2">Mechanism: </span>{strategy.mechanism}</div>
            <div><span className="text-ink-2">Physics model: </span>{strategy.physics_model}</div>
            <div><span className="text-ink-2">Construction: </span>{strategy.construction}</div>
            <div><span className="text-ink-2">Space: </span>{strategy.space}</div>
            <div><span className="text-ink-2">Model status: </span>{strategy.model_status}. {strategy.validation}</div>
            {strategy.uncertainty && <div><span className="text-ink-2">Uncertainty: </span>{strategy.uncertainty}</div>}
          </>
        )}
        <div><span className="text-ink-2">Strategies simulated: </span>{ev.resolved.join(" + ") || "none"}{ev.auto_added.length ? ` (dependencies added: ${ev.auto_added.join(", ")})` : ""}</div>
        {Object.keys(ev.parameters).length > 0 && <div><span className="text-ink-2">Parameters: </span>{Object.entries(ev.parameters).map(([k, v]) => `${k.split(".").slice(-1)[0]} = ${v}`).join(", ")}</div>}
        {ev.infeasible_reasons.length > 0 && <Callout level="error" title="Infeasible">{ev.infeasible_reasons.join("; ")}</Callout>}
        {ev.warnings.length > 0 && <Callout level="warning">{ev.warnings.join(" · ")}</Callout>}
        {ev.capex_incremental && ev.capex_incremental.lines.length > 0 && (
          <div>
            <div className="font-semibold mt-1">CAPEX changes (line items, counted once per scenario)</div>
            <table className="w-full tnum mt-1">
              <tbody>
                {ev.capex_incremental.lines.map((l) => (
                  <tr key={l.id} className="border-b border-line">
                    <td className="py-0.5 pr-2">{l.label}{l.scope === "shared" ? " (shared)" : ""}</td>
                    <td className="pr-2 text-ink-2">{l.delta_quantity > 0 ? "+" : ""}{fmtVal(l.delta_quantity, l.unit)} {l.unit}</td>
                    <td className="text-right">{l.delta_cost ? `${l.delta_cost.base > 0 ? "+" : ""}${fmtVal(l.delta_cost.base, "USD")}` : <StatusBadge status="insufficient_data" />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {ev.economics && <div className="text-ink-2">Payback / NPV: {ev.economics.note ?? (ev.economics.simple_payback_years != null ? `simple payback ${ev.economics.simple_payback_years.toFixed(1)} yr, NPV ${fmtVal(ev.economics.npv, "USD")}` : ev.economics.payback_note)}</div>}
      </div>
      <MetricTable metrics={ev.metrics} />
    </div>
  );
}

function MetricTable({ metrics, groups }: { metrics: ImpactMetric[]; groups?: string[] }) {
  const rows = groups ? metrics.filter((m) => groups.includes(m.group)) : metrics;
  return (
    <div className="overflow-auto max-h-[460px]">
      <table className="w-full text-[12px] tnum" data-testid="metric-table">
        <thead className="sticky top-0 bg-surface">
          <tr className="border-b border-line text-left"><th className="py-1 pr-2">Metric</th><th className="pr-2">Baseline</th><th className="pr-2">Scenario</th><th className="pr-2">Change</th><th>Status</th></tr>
        </thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.key} className="border-b border-line align-top" data-metric={m.key}>
              <td className="py-0.5 pr-2">{m.label} <span className="text-ink-3">({m.unit})</span></td>
              <td className="pr-2">{fmtVal(m.baseline, m.unit)}</td>
              <td className="pr-2" data-value={m.value ?? ""}>{fmtVal(m.value, m.unit)}{m.range ? <span className="text-ink-3"> [{fmtVal(m.range[0], m.unit)}–{fmtVal(m.range[1], m.unit)}]</span> : null}</td>
              <td className="pr-2"><DeltaText m={m} showStatus={false} /></td>
              <td title={m.note}><StatusBadge status={m.status} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------------------------------- C · builder
function Builder({ catalogue, applicability, sel, setSel, params, setParams, sharedLines, onRun, onPairs, running, pairRunning }: {
  catalogue: CatalogueStrategy[]; applicability: ImpactCatalogue["applicability"]; sel: string[]; setSel: (s: string[]) => void;
  params: Record<string, number>; setParams: (p: Record<string, number>) => void; sharedLines: { id: string; label: string; owners: string[] }[];
  onRun: () => void; onPairs: () => void; running: boolean; pairRunning: boolean;
}) {
  const { resolved, added, conflicts } = useMemo(() => resolveSelection(sel, catalogue), [sel, catalogue]);
  const by = Object.fromEntries(catalogue.map((c) => [c.id, c]));
  const shared = sharedLines.map((l) => ({ ...l, users: l.owners.filter((o) => resolved.includes(o)) })).filter((l) => l.users.length > 1);
  const toggle = (id: string, on: boolean) => setSel(on ? [...sel, id] : sel.filter((s) => s !== id));
  const paramsShown = resolved.flatMap((id) => by[id].parameters.map((p) => ({ ...p, owner: id })));
  return (
    <div className="grid xl:grid-cols-[1.4fr_1fr] gap-4">
      <div className="grid md:grid-cols-2 gap-2">
        {catalogue.map((c) => {
          const app = applicability?.[c.id];
          const on = sel.includes(c.id);
          const auto = added.includes(c.id);
          return (
            <div key={c.id} className="card p-3 flex flex-col gap-1" data-builder={c.id} style={on || auto ? { borderColor: "var(--seq-500)" } : undefined}>
              <div className="flex justify-between gap-2 items-start">
                <div className="min-w-0">
                  <div className="font-semibold text-[13px] leading-snug">{c.name}</div>
                  <div className="text-[11px] text-ink-3">{c.category}{c.requires.length ? ` · requires ${c.requires.map((r) => by[r]?.name ?? r).join(", ")}` : ""}{c.exclusive_group ? ` · one ${c.exclusive_group} option only` : ""}</div>
                </div>
                {app && !app.applicable ? <span className="text-[11px] text-ink-3" title={app.reasons.join("; ")}>not applicable</span> :
                  auto && !on ? <span className="text-[11.5px] whitespace-nowrap" title="Required by a selected strategy; included automatically">✓ included (dependency)</span> :
                  <Switch checked={on} onChange={(v) => toggle(c.id, v)} label="select" />}
              </div>
              <p className="text-[11.5px] text-ink-2">{c.description}</p>
              {app && !app.applicable && <p className="text-[11px]" style={{ color: "var(--status-critical)" }}>⛔ {app.reasons.join("; ")}</p>}
              {app?.warnings?.length ? <p className="text-[11px] text-ink-3">⚠ {app.warnings.join("; ")}</p> : null}
            </div>
          );
        })}
      </div>
      <div className="flex flex-col gap-3">
        <div className="card p-4 flex flex-col gap-2 text-[12px]">
          <div className="font-semibold text-[13px]">Selected configuration</div>
          {resolved.length === 0 ? <p className="text-ink-3">Baseline only — select one or more strategies.</p> : (
            <ul className="list-disc ml-4">{resolved.map((id) => <li key={id}>{by[id].name}{added.includes(id) ? " — added as a dependency" : ""}</li>)}</ul>
          )}
          {conflicts.map((c) => <Callout key={c} level="error" title="Incompatible">{c}</Callout>)}
          {shared.length > 0 && (
            <div className="text-ink-2"><span className="font-medium text-ink">Shared infrastructure (costed and sized once): </span>
              {shared.map((l) => `${l.label} — ${l.users.map((u) => by[u]?.name ?? u).join(", ")}`).join("; ")}</div>
          )}
          {paramsShown.length > 0 && (
            <div className="grid grid-cols-2 gap-2 mt-1">
              {paramsShown.map((p) => (
                <Field key={p.path} label={p.label} unit={p.unit} help={`${by[p.owner].name}; ${p.min}–${p.max}${params[p.path] === undefined ? " · project value" : " · changed"}`}>
                  <input className="input tnum" type="number" min={p.min} max={p.max} step={p.step} value={params[p.path] ?? p.value ?? ""} data-param={p.path}
                    onChange={(e) => {
                      const v = e.target.value;
                      const next = { ...params };
                      if (v === "" || Number(v) === p.value) delete next[p.path];
                      else next[p.path] = Number(v);
                      setParams(next);
                    }} />
                </Field>
              ))}
            </div>
          )}
          <div className="flex flex-wrap gap-2 mt-1">
            <button className="btn btn-primary" disabled={running || resolved.length === 0} onClick={onRun}>Run combination</button>
            <button className="btn" disabled={pairRunning || sel.length < 2 || sel.length > 10} onClick={onPairs} title="Simulates every pair among the selected strategies">Pairwise interactions</button>
            <button className="btn" onClick={() => { setSel([]); setParams({}); }}>Reset to baseline</button>
          </div>
          <p className="text-[11px] text-ink-3">The combination is simulated as one configuration (hourly physics, controller, plant, water, carbon) together with each selected strategy alone for attribution.</p>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------- D · results
function ResultsView({ ev, base, study, t }: { ev: ImpactEval; base: ImpactEval; study: ImpactStudy; t: Tokens }) {
  const m = byKey(ev.metrics);
  const res = study.result!;
  const period = `${res.period.start_utc.slice(0, 10)} – ${res.period.end_utc.slice(0, 10)}`;
  const src = `impact study ${study.id.slice(0, 8)} (configuration v${study.config_version_no}, model ${study.model_version})`;
  const cats = ["Baseline (S0)", "Combination"];
  const e = (x: ImpactEval) => byKey(x.metrics);
  const bm = e(base);
  const sp = (x: ImpactEval, k: string) => x.space?.categories?.[k]?.base ?? null;
  const tile = (k: string, label: string) => {
    const r = m[k];
    return <StatTile label={label} value={r?.value != null ? fmtVal(r.value, r.unit) : "—"} unit={r?.value != null ? r.unit : undefined} delta={r?.pct ?? null}
      sub={<span className="inline-flex flex-col gap-0.5">{r?.delta != null ? `${r.delta > 0 ? "+" : ""}${fmtVal(r.delta, r.unit)} ${r.unit}${r.pct === null && r.pct_note ? " (% n/a)" : ""}` : r?.note}<StatusBadge status={r?.status ?? "not_modelled"} /></span>} />;
  };
  return (
    <div className="flex flex-col gap-3" data-testid="combination-results">
      <div className="card p-3 text-[12.5px] flex flex-wrap gap-x-6 gap-y-1 items-center">
        <span><span className="text-ink-2">Strategies: </span><b>{ev.resolved.join(" + ")}</b>{ev.auto_added.length ? ` (dependencies added: ${ev.auto_added.join(", ")})` : ""}</span>
        {Object.keys(ev.parameters).length > 0 && <span><span className="text-ink-2">Parameters: </span>{Object.entries(ev.parameters).map(([k, v]) => `${k.split(".").slice(-1)[0]} = ${v}`).join(", ")}</span>}
        <span>{ev.feasible ? "✓ Feasible: all hard constraints met" : <><StatusBadge status="infeasible" /> {ev.infeasible_reasons.join("; ")}</>}</span>
      </div>
      {ev.warnings.length > 0 && <Callout level="warning">{ev.warnings.join(" · ")}</Callout>}
      {ev.simulated && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-2">
            {tile("cooling_mwh", "Cooling electricity")}{tile("facility_mwh", "Facility electricity")}{tile("pue", "PUE")}{tile("water_withdrawal_m3", "Water withdrawal")}
            {tile("co2e_t", "Operational CO₂e")}{tile("capex", "CAPEX")}{tile("site_at_grade_m2", "Footprint at grade")}{tile("underground_m2", "Underground area")}
          </div>
          <div className="grid lg:grid-cols-2 xl:grid-cols-3 gap-4">
            <ChartCard id="combo-energy" title="Annual electricity by component" units="MWh/yr" period={period} source={src}
              assumptions="IT load identical in both; 'other' = UPS/distribution losses, lighting, controls."
              option={barChart(t, { categories: cats, yName: "MWh/yr", unit: "MWh", legend: true, series: [
                { name: "IT", stack: "e", color: t.series[0], data: [bm.it_mwh?.value ?? null, m.it_mwh?.value ?? null] },
                { name: "Cooling", stack: "e", color: t.series[1], data: [bm.cooling_mwh?.value ?? null, m.cooling_mwh?.value ?? null] },
                { name: "Other facility", stack: "e", color: t.series[2], data: [bm.other_mwh?.value ?? null, m.other_mwh?.value ?? null] },
              ] })}
              table={{ columns: ["component", "baseline_mwh", "combination_mwh"], rows: ["it_mwh", "cooling_mwh", "other_mwh", "facility_mwh"].map((k) => [k, bm[k]?.value, m[k]?.value]) }} />
            <ChartCard id="combo-water" title="Annual site water" units="m³/yr" period={period} source={src}
              assumptions="Withdrawal = all site water drawn; consumption = evaporated/drift. Excludes water embedded in electricity."
              option={barChart(t, { categories: ["Withdrawal", "Consumption"], yName: "m³/yr", unit: "m³", legend: true, labels: true, series: [
                { name: "Baseline (S0)", color: t.neutral, data: [bm.water_withdrawal_m3?.value ?? null, bm.water_consumption_m3?.value ?? null] },
                { name: "Combination", color: t.series[0], data: [m.water_withdrawal_m3?.value ?? null, m.water_consumption_m3?.value ?? null] },
              ] })}
              table={{ columns: ["metric", "baseline", "combination", "status"], rows: ["water_withdrawal_m3", "water_consumption_m3", "wue_withdrawal", "wue_consumption"].map((k) => [k, bm[k]?.value, m[k]?.value, m[k]?.status]) }} />
            <ChartCard id="combo-carbon" title="Operational CO₂e" units="t CO₂e/yr" period={period} source={`${src}; grid factor as configured`}
              assumptions="Facility electricity × grid factor (hourly series when supplied). Embodied carbon of added materials is reported separately in the table."
              empty={m.co2e_t?.value == null ? m.co2e_t?.note || "No grid factor supplied." : undefined}
              option={barChart(t, { categories: cats, yName: "t/yr", unit: "t", labels: true, series: [{ name: "CO₂e", data: [bm.co2e_t?.value ?? null, m.co2e_t?.value ?? null], colors: [t.neutral, t.series[0]] }] })}
              table={{ columns: ["scenario", "co2e_t", "embodied_co2e_t"], rows: [["baseline", bm.co2e_t?.value, bm.embodied_co2e_t?.value], ["combination", m.co2e_t?.value, m.embodied_co2e_t?.value]] }} />
            <ChartCard id="combo-capex" title="Initial capital cost (CAPEX)" units={m.capex?.unit ?? "currency"} period="construction (rate basis year)" source={`${src}; project unit rates`}
              assumptions={`${study.result?.assumptions.unit_rate_sources?.join(", ") || "no"} rates; contingency ${((study.result?.assumptions.contingency_fraction ?? 0) * 100).toFixed(0)} %. Excludes ${study.result?.assumptions.excluded_costs?.join(", ")}.`}
              empty={m.capex?.value == null || bm.capex?.value == null ? (m.capex?.delta != null ? `Baseline CAPEX incomplete; incremental cost ${fmtVal(m.capex.delta, m.capex.unit)} ${m.capex.unit}.` : m.capex?.note || "Unit rates missing.") : undefined}
              option={barChart(t, { categories: cats, yName: m.capex?.unit ?? "", unit: m.capex?.unit ?? "", labels: true, series: [{ name: "CAPEX", data: [bm.capex?.value ?? null, m.capex?.value ?? null], colors: [t.neutral, t.series[0]] }] })}
              table={{ columns: ["line item", "Δ quantity", "unit", "Δ cost (base)", "Δ cost low", "Δ cost high"], rows: (ev.capex_incremental?.lines ?? []).map((l) => [l.label, l.delta_quantity, l.unit, l.delta_cost?.base ?? null, l.delta_cost?.low ?? null, l.delta_cost?.high ?? null]) }} />
            <ChartCard id="combo-space" title="Footprint at grade" units="m²" period="preliminary layout estimate" source={`${src}; layout ratios + configured geometry`}
              assumptions="Building (incl. shafts) + outdoor equipment yard. Roof, facade zone and underground areas are separate categories (table)."
              option={barChart(t, { categories: cats, yName: "m²", unit: "m²", legend: true, series: [
                { name: "Building footprint", stack: "s", color: t.series[0], data: [sp(base, "building_footprint"), sp(ev, "building_footprint")] },
                { name: "Outdoor equipment yard", stack: "s", color: t.series[1], data: [sp(base, "outdoor_yard"), sp(ev, "outdoor_yard")] },
              ] })}
              table={{ columns: ["category", "baseline_m2", "combination_m2", "status"], rows: Object.keys(ev.space?.categories ?? {}).map((k) => [k, sp(base, k), sp(ev, k), ev.space!.categories[k].status]) }} />
            <div className="card p-4 text-[12px] flex flex-col gap-1.5">
              <div className="font-semibold text-[13px]">Thermal feasibility and cooling</div>
              {["unmet_h", "violation_h", "max_inlet_c", "free_cooling_h", "natural_sufficient_h", "free_heat_mwh", "residual_mech_mwh", "peak_coil_kw", "installed_cooling_kw"].map((k) => m[k] && (
                <div key={k} className="flex justify-between gap-2 border-b border-line pb-0.5"><span className="text-ink-2">{m[k].label}</span>
                  <span className="tnum text-right">{fmtVal(m[k].value, m[k].unit)} {m[k].value != null ? m[k].unit : ""} <span className="text-ink-3">(baseline {fmtVal(m[k].baseline, m[k].unit)})</span> {m[k].status !== "modelled" && <StatusBadge status={m[k].status} compact />}</span></div>
              ))}
              {ev.economics && <p className="text-ink-2 mt-1">Payback / NPV: {ev.economics.note ?? (ev.economics.simple_payback_years != null ? `simple payback ${ev.economics.simple_payback_years.toFixed(1)} yr; NPV ${fmtVal(ev.economics.npv, m.capex?.unit ?? "USD")}` : ev.economics.payback_note)}</p>}
            </div>
          </div>
          <details className="card p-3">
            <summary className="cursor-pointer text-[13px] font-semibold">All metrics with status, ranges and notes</summary>
            <div className="mt-2"><MetricTable metrics={ev.metrics} /></div>
          </details>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------- E · attribution
function AttributionView({ study, catalogue, t }: { study: ImpactStudy; catalogue: CatalogueStrategy[]; t: Tokens }) {
  const at = study.result!.attribution!;
  const [key, setKey] = useState("facility_mwh");
  const by = Object.fromEntries(catalogue.map((c) => [c.id, c]));
  const row = at.rows.find((r) => r.key === key)!;
  const ids = Object.keys(row.individual);
  const cats = [...ids.map((i) => `${by[i]?.name ?? i} (alone)`), "Σ of individual changes (not simulated)", "Combination (simulated)"];
  const data = [...ids.map((i) => row.individual[i]), row.sum_individual, row.combined];
  const keyRows = at.rows.filter((r) => INTERACTION_KEYS.some(([k]) => k === r.key) || r.key === "pue");
  return (
    <div className="flex flex-col gap-3">
      <Callout level="info" title="How to read this">{at.note}</Callout>
      <div className="grid xl:grid-cols-[1fr_1.1fr] gap-4">
        <div className="flex flex-col gap-2">
          <ChartCard id="attribution" title={`Individual versus combined change — ${row.label}`} units={`${row.unit}, change vs baseline`} period="same period and baseline"
            source={`impact study ${study.id.slice(0, 8)}`} assumptions="Each bar except the sum is a separate hourly simulation; the sum is shown only to expose non-additivity."
            height={Math.max(200, cats.length * 30 + 50)}
            option={barChart(t, { categories: cats, horizontal: true, labels: true, yName: row.unit, unit: row.unit, series: [{ name: row.label, data,
              colors: [...ids.map(() => t.series[0]), t.neutral, t.series[1]] }] })}
            table={{ columns: ["bar", `change (${row.unit})`], rows: cats.map((c, i) => [c, data[i]]) }} />
          <div className="flex gap-2 flex-wrap text-[12px]" role="group" aria-label="Attribution metric">
            {INTERACTION_KEYS.map(([k, l]) => <button key={k} className={`btn !py-1 !px-2 ${k === key ? "btn-primary" : ""}`} aria-pressed={k === key} onClick={() => setKey(k)}>{l}</button>)}
          </div>
        </div>
        <div className="card overflow-auto">
          <table className="w-full text-[12px] tnum" data-testid="attribution-table">
            <thead><tr className="border-b border-line text-left"><th className="px-2 py-1.5">Metric</th>{ids.map((i) => <th key={i} className="px-2">{i} alone</th>)}<th className="px-2">Σ individual</th><th className="px-2">Combined</th><th className="px-2">Non-additive</th></tr></thead>
            <tbody>
              {keyRows.map((r) => (
                <tr key={r.key} className="border-b border-line">
                  <td className="px-2 py-1">{r.label} <span className="text-ink-3">({r.unit})</span></td>
                  {ids.map((i) => <td key={i} className="px-2">{fmtSigned(r.individual[i], r.unit)}</td>)}
                  <td className="px-2 text-ink-2">{fmtSigned(r.sum_individual, r.unit)}</td>
                  <td className="px-2 font-medium">{fmtSigned(r.combined, r.unit)}</td>
                  <td className="px-2">{fmtSigned(r.non_additive, r.unit)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

const fmtSigned = (v: number | null | undefined, unit: string) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${fmtVal(v, unit)}`);

function PairwiseView({ study, catalogue, t }: { study: ImpactStudy; catalogue: CatalogueStrategy[]; t: Tokens }) {
  const res = study.result!;
  const [key, setKey] = useState("facility_mwh");
  const by = Object.fromEntries(catalogue.map((c) => [c.id, c]));
  const ids = res.requested;
  const unit = byKey(res.baseline.metrics)[key]?.unit ?? "";
  const cell = (p: PairResult) => p.interactions[key];
  const data: [number, number, number | null][] = [];
  const cls: Record<string, string> = {};
  for (const p of res.pairs ?? []) {
    const i = ids.indexOf(p.a), j = ids.indexOf(p.b);
    const v = p.simulated ? cell(p)?.interaction ?? null : null;
    data.push([i, j, v], [j, i, v]);
    cls[`${i},${j}`] = cls[`${j},${i}`] = p.simulated ? cell(p)?.class ?? "not evaluated" : "infeasible";
  }
  const vals = data.map((d) => d[2]).filter((v): v is number => v !== null);
  const lim = Math.max(1e-9, ...vals.map(Math.abs));
  const sorted = [...(res.pairs ?? [])].sort((a, b) => Math.abs(cell(b)?.interaction ?? 0) - Math.abs(cell(a)?.interaction ?? 0));
  const names = ids.map((i) => SHORT[i] ?? i);
  return (
    <div className="grid xl:grid-cols-[1fr_1fr] gap-4">
      <div className="flex flex-col gap-2">
        <ChartCard id="pairwise" title={`Pairwise non-additivity — ${INTERACTION_KEYS.find(([k]) => k === key)?.[1]}`} units={`${unit}: Δ_AB − Δ_A − Δ_B`}
          period={`${res.period.start_utc.slice(0, 10)} – ${res.period.end_utc.slice(0, 10)}`} source={`impact study ${study.id.slice(0, 8)} (${res.simulations_run} simulations)`}
          assumptions="Negative (blue) = combined change lower than the sum of individual changes; positive (red) = higher. Letters: A additive, O overlapping, C complementary, X conflicting. Diagnostic only, not causal."
          height={Math.max(260, ids.length * 44 + 90)}
          empty={!vals.length ? "No simulated pairs with values for this metric." : undefined}
          option={heatmap(t, { x: names, y: names, data, unit, min: -lim, max: lim, colors: [t.divNeg, t.divMid, t.divPos],
            cellLabel: (_v, x, y) => CLASS_SHORT[cls[`${x},${y}`]] ?? "" })}
          table={{ columns: ["A", "B", "interaction", "class", "Σ individual", "combined"], rows: (res.pairs ?? []).map((p) => [p.a, p.b, cell(p)?.interaction ?? null, p.simulated ? cell(p)?.class : "infeasible", cell(p)?.sum_individual ?? null, cell(p)?.combined ?? null]) }} />
        <div className="flex gap-2 flex-wrap text-[12px]" role="group" aria-label="Interaction metric">
          {INTERACTION_KEYS.map(([k, l]) => <button key={k} className={`btn !py-1 !px-2 ${k === key ? "btn-primary" : ""}`} aria-pressed={k === key} onClick={() => setKey(k)}>{l}</button>)}
        </div>
      </div>
      <div className="card overflow-auto max-h-[520px]">
        <table className="w-full text-[12px] tnum" data-testid="pairs-table">
          <thead className="sticky top-0 bg-surface"><tr className="border-b border-line text-left"><th className="px-2 py-1.5">Pair</th><th className="px-2">Σ individual</th><th className="px-2">Combined</th><th className="px-2">Non-additive</th><th className="px-2">Class</th></tr></thead>
          <tbody>
            {sorted.map((p) => {
              const c = cell(p);
              return (
                <tr key={`${p.a}+${p.b}`} className="border-b border-line">
                  <td className="px-2 py-1" title={`${by[p.a]?.name} + ${by[p.b]?.name}`}>{SHORT[p.a] ?? p.a} + {SHORT[p.b] ?? p.b}</td>
                  {p.simulated ? (
                    <>
                      <td className="px-2">{fmtSigned(c?.sum_individual, unit)}</td>
                      <td className="px-2">{fmtSigned(c?.combined, unit)}</td>
                      <td className="px-2">{fmtSigned(c?.interaction, unit)}</td>
                      <td className="px-2">{CLASS_SHORT[c?.class ?? "not evaluated"]} · {c?.class}</td>
                    </>
                  ) : <td colSpan={4} className="px-2"><StatusBadge status="infeasible" /> {p.infeasible_reasons.join("; ")}</td>}
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="text-[11px] text-ink-3 p-2">Units {unit}. Classes: additive (within 2 % of the sum), overlapping (both reduce; together less than the sum), complementary (better than the sum), conflicting (worse than the better single strategy or than additive).</p>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------- F · export, pickers
function StudyPicker({ kind, studies, value, onChange }: { kind: string; studies?: ImpactStudy[]; value?: string; onChange: (id: string) => void }) {
  const list = (studies ?? []).filter((s) => s.kind === kind && s.status === "succeeded");
  if (list.length < 2) return null;
  return (
    <select className="input !w-auto !py-1 text-[12px]" value={value} onChange={(e) => onChange(e.target.value)} aria-label={`Choose ${kind} study`}>
      {list.map((s) => <option key={s.id} value={s.id}>{s.created_at.slice(0, 16).replace("T", " ")} · {(s.request.strategies ?? ["all"]).join("+")} · v{s.config_version_no}</option>)}
    </select>
  );
}

function ExportView({ studies }: { studies: ImpactStudy[] }) {
  if (!studies.length) return <Empty title="Nothing to export yet">Run a study first.</Empty>;
  return (
    <div className="card overflow-auto">
      <table className="w-full text-[12px]" data-testid="export-table">
        <thead><tr className="border-b border-line text-left"><th className="px-2 py-1.5">Created (UTC)</th><th className="px-2">Kind</th><th className="px-2">Strategies</th><th className="px-2">Config</th><th className="px-2">Status</th><th className="px-2">Download</th></tr></thead>
        <tbody>
          {studies.map((s) => (
            <tr key={s.id} className="border-b border-line">
              <td className="px-2 py-1">{s.created_at.slice(0, 19).replace("T", " ")}</td>
              <td className="px-2">{s.kind}</td>
              <td className="px-2">{(s.request.strategies ?? ["all applicable"]).join(" + ")}{Object.keys(s.request.parameters ?? {}).length ? ` (${Object.entries(s.request.parameters).map(([k, v]) => `${k.split(".").slice(-1)[0]}=${v}`).join(", ")})` : ""}</td>
              <td className="px-2">v{s.config_version_no} · {s.model_version}</td>
              <td className="px-2">{s.status}{s.error ? ` — ${s.error.slice(0, 120)}` : ""}</td>
              <td className="px-2 whitespace-nowrap">
                {s.status === "succeeded" && (["xlsx", "csv", "json"] as const).map((f) => <a key={f} className="btn !py-0.5 !px-2 mr-1" href={api.url(`/api/impact-studies/${s.id}/export?format=${f}`)}>{f.toUpperCase()}</a>)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[11px] text-ink-3 p-2">Exports contain inputs, dataset and period, selected strategies and parameters, baseline and scenario values, absolute and % changes, cost and space line items, assumptions, status labels and limitations.</p>
    </div>
  );
}

export { fmtPct };
