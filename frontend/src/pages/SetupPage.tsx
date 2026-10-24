import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useJob, usePatchProject, useProviders, useReferenceSites } from "../api/hooks";
import { Callout, JobBar, Section } from "../components/ui";
import { BoolField, diff, EditorCtx, Group, NumField, SelectField, setPath, TextField } from "../forms/ConfigEditor";
import { useProjectPage } from "../lib/project";
import { SiteMap } from "../maps/SiteMap";
import type { Assumption } from "../types/api";

const SCENARIOS: [string, string][] = [
  ["S0", "Conventional baseline"], ["S1", "Improved conventional"], ["S2", "Passive envelope"], ["S3", "Stack-assisted"],
  ["S4", "Earth-air HX"], ["S5", "Adaptive facade"], ["S6", "Low-water"], ["S7", "Biomimetic hybrid (search)"], ["S8", "User-defined"],
];

export function SetupPage() {
  const { projectId, project } = useProjectPage();
  const p = project.data;
  const nav = useNavigate();
  const qc = useQueryClient();
  const patch = usePatchProject(projectId);
  const [draft, setDraft] = useState<any>(null);
  useEffect(() => {
    if (p) setDraft(structuredClone(p.config));
  }, [p]);
  const sources = useMemo(() => Object.fromEntries((p?.assumptions ?? []).map((a: Assumption) => [a.path, a])), [p]);
  const changes = useMemo(() => (p && draft ? diff(p.config, draft) : {}), [p, draft]);
  const nChanges = JSON.stringify(changes).match(/:/g)?.length ?? 0;
  const defaults = (p?.assumptions ?? []).filter((a) => a.source === "default-assumption").length;

  const [scenarios, setScenarios] = useState<string[]>(SCENARIOS.map((s) => s[0]));
  const [runJob, setRunJob] = useState<{ job: string; sim: string } | null>(null);
  const job = useJob(runJob?.job);
  const run = useMutation({
    mutationFn: () => api.post<any>("/api/simulations", { project_id: projectId, scenarios }),
    onSuccess: (r) => setRunJob({ job: r.job_id, sim: r.simulation_id }),
  });
  useEffect(() => {
    if (job.data?.status === "succeeded" && runJob) {
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      nav(`/projects/${projectId}/compare?sim=${runJob.sim}`);
    }
  }, [job.data?.status, runJob, nav, projectId, qc]);

  if (project.isLoading || !p || !draft) return <p className="text-ink-2">Loading project…</p>;
  if (project.error) return <Callout level="error">{String(project.error)}</Callout>;
  const set = (path: string, v: unknown) => setDraft((d: any) => setPath(d, path, v));

  return (
    <EditorCtx.Provider value={{ draft, set, sources }}>
      <div className="flex flex-col gap-5">
        <div className="flex flex-wrap items-end gap-3 justify-between">
          <div>
            <h1 className="text-[20px] font-semibold">Project setup</h1>
            <p className="text-[12px] text-ink-2">
              Configuration version {p.current_version_no} · hash {p.config_hash.slice(0, 12)} · {defaults} of {p.assumptions.length} inputs are
              default assumptions (marked ⚠ assumed) — replace them with project data where available.
            </p>
          </div>
          <div className="flex gap-2 items-center sticky top-2">
            {patch.error && <span className="text-[12px]" style={{ color: "var(--status-critical)" }}>⛔ {patch.error.message}</span>}
            <button className="btn" disabled={!nChanges} onClick={() => setDraft(structuredClone(p.config))}>Discard</button>
            <button className="btn btn-primary" disabled={!nChanges || patch.isPending}
              onClick={() => patch.mutate({ patch: changes, note: "edited on setup page" })}>
              Save as version {p.current_version_no + 1}{nChanges ? ` (${nChanges} change${nChanges > 1 ? "s" : ""})` : ""}
            </button>
          </div>
        </div>
        {p.compatibility.length > 0 && (
          <div className="flex flex-col gap-1">
            {p.compatibility.map((c, i) => <Callout key={i} level={c.level === "conflict" ? "error" : "warning"} title={c.strategies.join(", ")}>{c.message}</Callout>)}
          </div>
        )}

        <div className="grid xl:grid-cols-[1fr_420px] gap-4">
          <div className="flex flex-col gap-4">
            <Group title="Site and location">
              <TextField path="name" label="Project name" />
              <TextField path="site.name" label="Site name" />
              <TextField path="site.country" label="Country" />
              <TextField path="site.address" label="Address / site description" />
              <NumField path="site.latitude" label="Latitude" unit="°" min={-90} max={90} />
              <NumField path="site.longitude" label="Longitude" unit="°" min={-180} max={180} />
              <NumField path="site.elevation_m" label="Elevation" unit="m" />
              <TextField path="site.timezone" label="Time zone (IANA)" />
              <NumField path="site.site_area_m2" label="Site area" unit="m²" nullable />
              <NumField path="site.land_available_for_buried_m2" label="Land for buried infrastructure" unit="m²" nullable />
              <SelectField path="site.terrain" label="Terrain (wind profile)" options={["large_city", "urban_suburban", "open", "flat_unobstructed"]} />
            </Group>
          </div>
          <div className="card p-3 flex flex-col gap-2">
            <SiteMap markers={[{ lat: draft.site.latitude, lon: draft.site.longitude, label: draft.site.name, kind: "site" }]}
              onPick={(a, b) => { set("site.latitude", a); set("site.longitude", b); }} height={300} />
            <p className="text-[11px] text-ink-3">Click to move the site. The nearest weather source is not assumed to represent the site: distance and elevation difference are recorded with each dataset.</p>
          </div>
        </div>

        <Group title="IT load and thermal requirements" note="Installed capacity is never used as energy directly: hourly IT power = capacity × utilisation profile.">
          <NumField path="it.installed_capacity_mw" label="Installed IT capacity" unit="MW" min={0.01} />
          <SelectField path="it.utilisation_scenario" label="Load scenario" options={[["expected", "Expected (uses mean utilisation)"], ["low", "Low (35 %)"], ["peak", "Peak (90 %)"], ["ai_hpc", "AI/HPC (85 %)"], ["custom", "Custom profile"]]} />
          <NumField path="it.average_utilisation" label="Mean utilisation" unit="–" min={0.01} max={1} step={0.01} />
          <NumField path="it.diurnal_amplitude" label="Diurnal swing" unit="–" min={0} max={0.5} step={0.01} />
          <NumField path="it.annual_growth" label="Annual load growth" unit="–" step={0.01} />
          <NumField path="it.rack_count" label="Racks" min={1} />
          <NumField path="it.avg_rack_kw" label="Average rack density" unit="kW" />
          <NumField path="it.max_rack_kw" label="Peak rack density" unit="kW" />
          <NumField path="it.server_delta_t_k" label="Server air ΔT" unit="K" />
          <NumField path="it.inlet_setpoint_c" label="Inlet setpoint" unit="°C" />
          <SelectField path="it.ashrae_class" label="ASHRAE class" options={["A1", "A2", "A3", "A4", "H1"]} />
          <SelectField path="it.envelope_mode" label="Hard constraint" options={[["recommended", "Recommended envelope"], ["allowable", "Allowable envelope"]]} />
          <NumField path="it.economiser_supply_margin_k" label="Economiser safety margin" unit="K" />
          <SelectField path="it.containment" label="Aisle containment" options={["full", "partial", "none"]} />
          <NumField path="it.liquid_cooled_fraction" label="Direct liquid-cooled share" unit="–" min={0} max={0.95} step={0.05} />
          <NumField path="it.liquid_supply_c" label="Liquid supply temperature" unit="°C" />
          <NumField path="it.uptime_target_pct" label="Uptime target" unit="%" />
          <NumField path="it.design_life_years" label="Design life" unit="years" />
        </Group>

        <Group title="Electrical and facility loads">
          <NumField path="electrical.ups_capacity_factor" label="UPS capacity / IT capacity" unit="–" />
          <SelectField path="electrical.electrical_redundancy" label="Electrical redundancy" options={["N", "N+1", "2N", "2N+1"]} />
          <NumField path="electrical.distribution_loss_fraction" label="Distribution losses" unit="– of IT" step={0.001} />
          <NumField path="electrical.lighting_w_m2" label="Lighting" unit="W/m²" />
          <NumField path="electrical.other_fixed_kw" label="Other facility loads" unit="kW" />
          <NumField path="electrical.losses_in_cooled_space_fraction" label="Losses inside cooled space" unit="–" step={0.1} />
        </Group>

        <Group title="Building geometry and envelope">
          <NumField path="building.length_m" label="Length" unit="m" />
          <NumField path="building.width_m" label="Width" unit="m" />
          <NumField path="building.height_m" label="Height" unit="m" />
          <NumField path="building.floors" label="Floors" min={1} />
          <NumField path="building.orientation_deg" label="Long-facade azimuth" unit="°" />
          <NumField path="building.window_area_m2" label="Window area" unit="m²" />
          <NumField path="building.window_u" label="Window U" unit="W/m²K" />
          <NumField path="building.window_shgc" label="Window SHGC" unit="–" />
          <NumField path="building.insulation_m" label="Insulation thickness" unit="m" step={0.01} />
          <NumField path="building.thermal_mass_m" label="Concrete mass layer" unit="m" step={0.01} />
          <NumField path="building.roof_solar_reflectance" label="Roof solar reflectance" unit="–" step={0.05} />
          <NumField path="building.roof_emissivity" label="Roof emissivity" unit="–" step={0.05} />
          <NumField path="building.wall_solar_absorptance" label="Wall solar absorptance" unit="–" step={0.05} />
          <NumField path="building.thermal_bridge_factor" label="Thermal-bridge factor" unit="–" step={0.05} />
          <NumField path="building.infiltration_ach" label="Infiltration" unit="ACH" step={0.01} />
          <NumField path="building.min_outdoor_air_m3s" label="Minimum outdoor air" unit="m³/s" />
        </Group>

        <Group title="Conventional reference (baseline) cooling" note="All scenarios share this mechanical plant capacity and redundancy so comparisons are fair.">
          <SelectField path="baseline.architecture" label="Architecture" options={[
            ["air_cooled_chiller_crah", "Air-cooled chiller + CRAH"], ["water_cooled_chiller_tower", "Water-cooled chiller + cooling tower"],
            ["dx_crac", "Air-cooled DX (CRAC)"], ["dx_with_airside_economiser", "DX + air-side economiser"], ["direct_evaporative", "Direct evaporative + DX trim"],
            ["indirect_evaporative", "Indirect evaporative + DX trim"], ["liquid_cooling_dry_cooler", "Liquid cooling + dry cooler"]]} />
          <SelectField path="baseline.chiller_model" label="Performance model" options={[["carnot", "Carnot-fraction (calibrated to rated COP)"], ["biquadratic", "Manufacturer biquadratic curves"], ["constant", "Constant COP (preliminary)"]]} />
          <NumField path="baseline.rated_cop" label="Rated COP (blank = default)" unit="–" nullable />
          <NumField path="baseline.chw_supply_c" label="Chilled-water supply" unit="°C" />
          <NumField path="baseline.chw_delta_t_k" label="Chilled-water ΔT" unit="K" />
          <SelectField path="baseline.cooling_redundancy" label="Cooling redundancy" options={["N", "N+1", "N+2", "2N"]} />
          <NumField path="baseline.n_units" label="Duty units (N)" min={1} />
          <NumField path="baseline.design_margin" label="Design margin" unit="–" step={0.05} />
          <NumField path="baseline.crah_dp_pa" label="CRAH static pressure" unit="Pa" />
          <NumField path="baseline.crah_fan_efficiency" label="Fan efficiency" unit="–" step={0.01} />
          <NumField path="baseline.tower_approach_k" label="Tower approach" unit="K" />
          <NumField path="baseline.tower_cycles" label="Cycles of concentration" unit="–" />
          <SelectField path="baseline.humidification" label="Humidification" options={["adiabatic", "steam", "none"]} />
          <BoolField path="baseline.waterside_economiser" label="Water-side economiser" />
          <BoolField path="baseline.dehumidification_allowed" label="Mechanical dehumidification allowed" />
        </Group>

        <Group title="Water and environmental constraints" note="A basin water-stress index is context, not a site withdrawal permit; enter permits separately.">
          <SelectField path="site.constraints.water_source" label="Water source" options={["municipal_potable", "reclaimed", "groundwater", "surface", "mixed", "unknown"]} />
          <NumField path="site.constraints.permitted_annual_withdrawal_m3" label="Permitted annual withdrawal" unit="m³" nullable />
          <NumField path="site.constraints.permitted_daily_withdrawal_m3" label="Permitted daily withdrawal" unit="m³" nullable />
          <NumField path="site.constraints.reclaimed_water_fraction" label="Reclaimed-water share" unit="–" step={0.05} />
          <NumField path="site.constraints.water_stress_score" label="Water stress (Aqueduct 0–5)" unit="–" nullable />
          <TextField path="site.constraints.water_stress_source" label="Water-stress source" />
          <NumField path="site.constraints.groundwater_depth_m" label="Groundwater depth" unit="m" nullable />
          <SelectField path="site.constraints.dust_exposure" label="Dust exposure" options={["low", "medium", "high", "unknown"]} />
          <SelectField path="site.constraints.salinity_exposure" label="Salinity exposure" options={["low", "medium", "high", "unknown"]} />
          <SelectField path="site.constraints.flood_exposure" label="Flood exposure" options={["low", "medium", "high", "unknown"]} />
          <SelectField path="site.constraints.radon_risk" label="Radon / soil-gas risk" options={["low", "medium", "high", "unknown"]} />
          <NumField path="site.constraints.noise_limit_dba" label="Noise limit" unit="dB(A)" nullable />
          <BoolField path="site.constraints.gaseous_contamination_high" label="High gaseous contamination" />
          <BoolField path="site.constraints.groundwater_protection_zone" label="Groundwater protection zone" />
        </Group>

        <Group title="Grid carbon and economics" note="No default grid factor is shipped: cite the source (e.g. EPA eGRID subregion, CEA CO₂ database, Ember).">
          <NumField path="grid.emission_factor_kg_per_kwh" label="Grid emission factor" unit="kg CO₂e/kWh" nullable />
          <SelectField path="grid.factor_type" label="Factor type" options={["average", "marginal"]} />
          <SelectField path="grid.accounting_basis" label="Accounting basis" options={["location", "market"]} />
          <TextField path="grid.source" label="Factor source" />
          <NumField path="grid.marginal_factor_kg_per_kwh" label="Marginal factor (scenario)" unit="kg/kWh" nullable />
          <NumField path="grid.contracted_renewable_fraction" label="Contracted renewables (market)" unit="–" step={0.05} />
          <NumField path="economics.electricity_price_per_kwh" label="Electricity price" unit="/kWh" nullable />
          <NumField path="economics.water_price_per_m3" label="Water price" unit="/m³" nullable />
          <TextField path="economics.currency" label="Currency" />
          <NumField path="economics.discount_rate" label="Discount rate" unit="–" step={0.01} />
          <NumField path="economics.horizon_years" label="Analysis horizon" unit="years" />
        </Group>

        <ClimateData />

        <Section title="Run simulations">
          <div className="card p-4 flex flex-col gap-3">
            <p className="text-[12px] text-ink-2">Every scenario uses the same climate dataset, IT load profile, envelope class and redundancy. Save configuration changes first.</p>
            <div className="flex flex-wrap gap-3">
              {SCENARIOS.map(([id, label]) => (
                <label key={id} className="flex items-center gap-1.5 text-[13px]">
                  <input type="checkbox" checked={scenarios.includes(id)} onChange={(e) => setScenarios((s) => (e.target.checked ? [...s, id] : s.filter((x) => x !== id)))} />
                  <b>{id}</b> {label}
                </label>
              ))}
            </div>
            {!p.default_dataset_id && <Callout level="warning">Select, retrieve or upload climate data first.</Callout>}
            {run.error && <Callout level="error">{run.error.message}</Callout>}
            <div className="flex gap-2 items-center">
              <button className="btn btn-primary" disabled={!p.default_dataset_id || nChanges > 0 || run.isPending || !scenarios.length || (!!job.data && ["queued", "running"].includes(job.data.status))}
                onClick={() => run.mutate()}>Run {scenarios.length} scenario{scenarios.length !== 1 ? "s" : ""}</button>
              {nChanges > 0 && <span className="text-[12px] text-ink-2">Unsaved changes — save before running.</span>}
              {job.data && ["queued", "running"].includes(job.data.status) && (
                <button className="btn" onClick={() => api.post(`/api/jobs/${runJob!.job}/cancel`)}>Cancel</button>
              )}
            </div>
            {runJob && <JobBar job={job.data} label="Simulation" />}
          </div>
        </Section>
      </div>
    </EditorCtx.Provider>
  );
}

function ClimateData() {
  const { projectId, project } = useProjectPage();
  const p = project.data!;
  const qc = useQueryClient();
  const refs = useReferenceSites();
  const providers = useProviders();
  const patch = usePatchProject(projectId);
  const [refSite, setRefSite] = useState("");
  const [start, setStart] = useState("2015-01-01");
  const [end, setEnd] = useState("2024-12-31");
  const [prov, setProv] = useState<string[]>(["open_meteo", "nasa_power"]);
  const [fetchJob, setFetchJob] = useState<string | null>(null);
  const job = useJob(fetchJob);
  const [file, setFile] = useState<File | null>(null);
  const [tz, setTz] = useState("UTC");
  const [nature, setNature] = useState("observed");
  useEffect(() => {
    if (job.data?.status === "succeeded" && job.data.result?.dataset_id) {
      patch.mutate({ patch: {}, default_dataset_id: job.data.result.dataset_id });
      setFetchJob(null);
    }
  }, [job.data?.status, job.data?.result, patch]);
  const attach = useMutation({
    mutationFn: () => api.post<any>("/api/climate/reference", { project_id: projectId, site_id: refSite }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project", projectId] }),
  });
  const fetcher = useMutation({
    mutationFn: () => api.post<any>("/api/climate/fetch", { project_id: projectId, latitude: p.config.site.latitude, longitude: p.config.site.longitude,
      elevation_m: p.config.site.elevation_m, start, end, providers: prov }),
    onSuccess: (r) => setFetchJob(r.job_id),
  });
  const upload = useMutation({
    mutationFn: () => {
      const f = new FormData();
      f.append("file", file!);
      f.append("latitude", String(p.config.site.latitude));
      f.append("longitude", String(p.config.site.longitude));
      f.append("elevation_m", String(p.config.site.elevation_m));
      f.append("project_id", projectId);
      f.append("timezone", tz);
      f.append("data_nature", nature);
      f.append("description", file!.name);
      return api.upload<any>("/api/climate/upload", f);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project", projectId] }),
  });
  return (
    <Section title="Climate data">
      <div className="card p-4 flex flex-col gap-4">
        {p.datasets.length > 0 ? (
          <table className="w-full text-[12px]">
            <thead><tr className="text-left border-b border-line"><th className="py-1">Use</th><th>Provider</th><th>Product</th><th>Nature</th><th>Hours</th><th>Period (UTC)</th><th>Distance</th></tr></thead>
            <tbody>
              {p.datasets.map((d) => (
                <tr key={d.id} className="border-b border-line">
                  <td className="py-1"><input type="radio" name="ds" aria-label={`Use dataset ${d.product}`} checked={p.default_dataset_id === d.id}
                    onChange={() => patch.mutate({ patch: {}, default_dataset_id: d.id })} /></td>
                  <td>{d.provider}</td><td className="max-w-[340px] truncate" title={d.product}>{d.product}</td><td>{d.data_nature}</td>
                  <td className="tnum">{d.n_hours}</td><td>{d.start_utc.slice(0, 10)} – {d.end_utc.slice(0, 10)}</td>
                  <td className="tnum">{d.distance_km != null ? `${d.distance_km.toFixed(1)} km` : "n/a"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Callout level="warning">No climate dataset attached to this project.</Callout>}
        <div className="grid md:grid-cols-3 gap-4">
          <div className="flex flex-col gap-2">
            <div className="font-semibold text-[13px]">Bundled reference (offline)</div>
            <select className="input" value={refSite} onChange={(e) => setRefSite(e.target.value)} aria-label="Reference weather site">
              <option value="">Choose a TMY3 site…</option>
              {refs.data?.sites.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
            </select>
            <button className="btn self-start" disabled={!refSite || attach.isPending} onClick={() => attach.mutate()}>Attach</button>
            <p className="text-[11px] text-ink-3">NREL TMY3 typical years (real data, composite months). The distance to your site is recorded.</p>
          </div>
          <div className="flex flex-col gap-2">
            <div className="font-semibold text-[13px]">Retrieve reanalysis / modeled data</div>
            <div className="flex gap-2">
              <input className="input" type="date" value={start} onChange={(e) => setStart(e.target.value)} aria-label="Start date" />
              <input className="input" type="date" value={end} onChange={(e) => setEnd(e.target.value)} aria-label="End date" />
            </div>
            <div className="flex flex-wrap gap-3 text-[12px]">
              {(providers.data ?? []).filter((x) => ["open_meteo", "nasa_power", "cds_era5"].includes(x.id)).map((x) => (
                <label key={x.id} className="flex items-center gap-1" title={x.label}>
                  <input type="checkbox" checked={prov.includes(x.id)} disabled={!x.configured} onChange={(e) => setProv((s) => (e.target.checked ? [...s, x.id] : s.filter((y) => y !== x.id)))} />
                  {x.id}{!x.configured ? " (not configured)" : ""}
                </label>
              ))}
            </div>
            <button className="btn self-start" disabled={fetcher.isPending || !prov.length} onClick={() => fetcher.mutate()}>Retrieve</button>
            {fetchJob && <JobBar job={job.data} label="Climate retrieval" />}
            <p className="text-[11px] text-ink-3">Providers are tried in order; any fallback is recorded in the dataset provenance. Requires network access to the provider.</p>
          </div>
          <div className="flex flex-col gap-2">
            <div className="font-semibold text-[13px]">Upload (CSV, EPW, NetCDF)</div>
            <input className="text-[12px]" type="file" accept=".csv,.epw,.nc,.nc4,.txt,.gz" onChange={(e) => setFile(e.target.files?.[0] ?? null)} aria-label="Climate file" />
            <div className="flex gap-2">
              <input className="input" value={tz} onChange={(e) => setTz(e.target.value)} aria-label="Timestamp time zone" title="Time zone of CSV timestamps" />
              <select className="input" value={nature} onChange={(e) => setNature(e.target.value)} aria-label="Data nature">
                {["observed", "reanalysis", "typical_year", "modeled", "manual"].map((n) => <option key={n}>{n}</option>)}
              </select>
            </div>
            <button className="btn self-start" disabled={!file || upload.isPending} onClick={() => upload.mutate()}>Upload</button>
            {upload.error && <Callout level="error">{upload.error.message}</Callout>}
            {upload.data?.quality?.warnings?.map((w: string, i: number) => <Callout key={i} level="warning">{w}</Callout>)}
          </div>
        </div>
      </div>
    </Section>
  );
}
