import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useJob, useProjects, useReferenceSites } from "../api/hooks";
import { Callout, Empty, Field, JobBar, Section } from "../components/ui";
import { SiteMap } from "../maps/SiteMap";

export function ProjectsPage() {
  const projects = useProjects();
  const refs = useReferenceSites();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [name, setName] = useState("New data-center project");
  const [lat, setLat] = useState<number | "">("");
  const [lon, setLon] = useState<number | "">("");
  const [elev, setElev] = useState<number | "">("");
  const [tz, setTz] = useState("UTC");
  const [refSite, setRefSite] = useState("");
  const [query, setQuery] = useState("");
  const [geo, setGeo] = useState<any>(null);
  const [demoJob, setDemoJob] = useState<{ job: string; project: string } | null>(null);
  const job = useJob(demoJob?.job);

  const geocode = useMutation({ mutationFn: (q: string) => api.post<any>("/api/sites/geocode", { query: q }), onSuccess: setGeo });
  const create = useMutation({
    mutationFn: () =>
      api.post<any>("/api/projects", {
        config: { name, site: { name, latitude: Number(lat), longitude: Number(lon), elevation_m: Number(elev || 0), timezone: tz } },
        reference_site_id: refSite || null,
      }),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      nav(`/projects/${p.id}/setup`);
    },
  });
  const demo = useMutation({
    mutationFn: (site: string) => api.post<any>(`/api/demo?site_id=${site}`),
    onSuccess: (r) => {
      setDemoJob({ job: r.job_id, project: r.project_id });
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  const pickRef = (id: string) => {
    setRefSite(id);
    const s = refs.data?.sites.find((x) => x.id === id);
    if (s) {
      setLat(s.latitude);
      setLon(s.longitude);
      setElev(s.elevation_m);
      setTz(s.timezone);
      if (name === "New data-center project") setName(`${s.label} project`);
    }
  };
  const valid = lat !== "" && lon !== "" && Math.abs(Number(lat)) <= 90 && Math.abs(Number(lon)) <= 180;
  const markers = [
    ...(valid ? [{ lat: Number(lat), lon: Number(lon), label: "Proposed site", kind: "site" as const }] : []),
    ...(projects.data ?? []).filter((p) => p.site).map((p) => ({ lat: p.site!.latitude, lon: p.site!.longitude, label: p.name, kind: "other" as const })),
  ];

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-[22px] font-semibold">Second Nature</h1>
        <p className="text-ink-2 max-w-3xl mt-1">
          Climate-informed engineering workbench for data-center cooling. It compares a conventional reference facility with
          hybrid designs that add biomimetic passive and low-energy strategies, using site weather, explicit physical models and
          stated assumptions. Results are screening estimates for preliminary design.
        </p>
      </div>

      <div className="grid lg:grid-cols-[1.1fr_1fr] gap-5">
        <Section title="Projects">
          {projects.isLoading ? <p className="text-ink-2">Loading…</p> : projects.data?.length ? (
            <div className="flex flex-col gap-2">
              {projects.data.map((p) => (
                <Link key={p.id} to={`/projects/${p.id}/setup`} className="card px-4 py-3 hover:bg-[var(--surface-2)] flex justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-semibold truncate">{p.name}</div>
                    <div className="text-[12px] text-ink-2">
                      {p.site ? `${p.site.latitude.toFixed(3)}, ${p.site.longitude.toFixed(3)} · ${p.site.timezone}` : "no site"} · version {p.current_version_no}
                      {p.is_demo ? " · demo" : ""}
                    </div>
                  </div>
                  <span className="text-[12px] text-ink-3 whitespace-nowrap">{p.updated_at?.slice(0, 10)}</span>
                </Link>
              ))}
            </div>
          ) : (
            <Empty title="No projects yet">Create a project from a site, or start the demonstration project (real TMY3 weather for Ashburn, Virginia; all other inputs are labeled assumptions).</Empty>
          )}
          <div className="card p-4 flex flex-col gap-2">
            <div className="font-semibold">Demonstration projects</div>
            <p className="text-[12px] text-ink-2">Creates a project with bundled NREL TMY3 weather, then runs all nine scenarios in the background (about 30 s).</p>
            <div className="flex flex-wrap gap-2">
              {[["ashburn_va", "Ashburn, VA (mixed-humid)"], ["phoenix_az", "Phoenix, AZ (hot-dry)"], ["miami_fl", "Miami, FL (hot-humid)"]].map(([id, label]) => (
                <button key={id} className="btn" disabled={demo.isPending || (job.data && !["succeeded", "failed"].includes(job.data.status))} onClick={() => demo.mutate(id)}>
                  {label}
                </button>
              ))}
            </div>
            {demo.error && <Callout level="error">{String(demo.error.message)}</Callout>}
            {demoJob && <JobBar job={job.data} label="Demo simulation" />}
            {job.data?.status === "succeeded" && demoJob && (
              <Link className="btn btn-primary self-start" to={`/projects/${demoJob.project}/compare`}>Open demo results</Link>
            )}
          </div>
        </Section>

        <Section title="New project">
          <div className="card p-4 flex flex-col gap-3">
            <Field label="Project name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
            <div className="flex gap-2 items-end">
              <Field label="Find a place (geocoding)">
                <input className="input" value={query} placeholder="e.g. Chennai, India" onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && query.length > 1 && geocode.mutate(query)} />
              </Field>
              <button className="btn" disabled={query.length < 2 || geocode.isPending} onClick={() => geocode.mutate(query)}>Search</button>
            </div>
            {geo && (geo.results?.length ? (
              <ul className="text-[12px] flex flex-col gap-1">
                {geo.results.map((r: any, i: number) => (
                  <li key={i}>
                    <button className="underline text-left" onClick={() => { setLat(r.latitude); setLon(r.longitude); if (r.elevation_m != null) setElev(r.elevation_m); if (r.timezone) setTz(r.timezone); }}>
                      {r.name}
                    </button> <span className="text-ink-3">({r.source}{r.cached ? ", cached" : ""})</span>
                  </li>
                ))}
              </ul>
            ) : <Callout level="warning">{geo.fallback} {geo.errors?.join("; ")}</Callout>)}
            <SiteMap markers={markers} onPick={(a, b) => { setLat(a); setLon(b); }} height={260} />
            <p className="text-[11px] text-ink-3">Click the map to set coordinates. Other projects are shown as secondary markers.</p>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              <Field label="Latitude" unit="°"><input className="input" type="number" value={lat} onChange={(e) => setLat(e.target.value === "" ? "" : Number(e.target.value))} /></Field>
              <Field label="Longitude" unit="°"><input className="input" type="number" value={lon} onChange={(e) => setLon(e.target.value === "" ? "" : Number(e.target.value))} /></Field>
              <Field label="Elevation" unit="m"><input className="input" type="number" value={elev} onChange={(e) => setElev(e.target.value === "" ? "" : Number(e.target.value))} /></Field>
              <Field label="Time zone (IANA)"><input className="input" value={tz} onChange={(e) => setTz(e.target.value)} /></Field>
            </div>
            <Field label="Climate data" help="Bundled typical-year reference data work offline. Reanalysis data (Open-Meteo, NASA POWER, ERA5) can be retrieved on the Setup page.">
              <select className="input" value={refSite} onChange={(e) => pickRef(e.target.value)}>
                <option value="">Select later (retrieve or upload on the Setup page)</option>
                {refs.data?.sites.map((s) => <option key={s.id} value={s.id}>{s.label} — TMY3 {s.climate}</option>)}
              </select>
            </Field>
            {create.error && <Callout level="error">{create.error.message}</Callout>}
            <button className="btn btn-primary self-start" disabled={!valid || create.isPending} onClick={() => create.mutate()}>Create project</button>
          </div>
        </Section>
      </div>
    </div>
  );
}
