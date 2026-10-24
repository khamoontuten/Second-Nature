import { useMemo, useState } from "react";
import { api } from "../api/client";
import { useClimateSeries, useClimateSummary } from "../api/hooks";
import { ChartCard } from "../charts/ChartCard";
import { barChart, boxplot, heatmap, lineChart, scatter, windRose } from "../charts/options";
import { useTokens } from "../charts/useTokens";
import { Callout, Empty, Section, StatTile } from "../components/ui";
import { fmt, MONTHS, quantiles } from "../lib/format";
import { useProjectPage } from "../lib/project";

export function ClimatePage() {
  const { project, datasetId, tz, elevation } = useProjectPage();
  const t = useTokens();
  const summary = useClimateSummary(datasetId, tz, elevation);
  const series = useClimateSeries(datasetId, "t_db_c,rh_pct,t_dp_c,t_wb_c,ghi_wm2,wind_ms", 1, elevation);
  const [month, setMonth] = useState<number>(0);
  const [inspect, setInspect] = useState("");

  const s = summary.data?.summary;
  const prov = summary.data?.provenance ?? {};
  const qc = summary.data?.quality;
  const period = s ? `${s.period.start_utc.slice(0, 10)} – ${s.period.end_utc.slice(0, 10)} (UTC), ${s.period.hours} h, ${s.period.data_nature}` : "";
  const sourceText = summary.data ? `${summary.data.product} (${summary.data.provider}); distance to site ${fmt(prov.distance_km, 1)} km` : "";

  const ts = series.data;
  const idx = useMemo(() => (ts ? ts.index.map((x) => new Date(x).getTime()) : []), [ts]);
  const col = (k: string) => (ts?.columns[k] ?? []) as (number | null)[];
  const sel = useMemo(() => {
    if (!ts || month === 0) return idx.map((_, i) => i);
    return idx.map((ms, i) => (new Date(ms).getUTCMonth() + 1 === month ? i : -1)).filter((i) => i >= 0);
  }, [idx, ts, month]);

  if (!datasetId) return <Empty title="No climate dataset">Attach bundled reference data, retrieve reanalysis data or upload a file on the Setup page.</Empty>;
  if (summary.isLoading) return <p className="text-ink-2">Computing climate summary…</p>;
  if (summary.error) return <Callout level="error">{String(summary.error)}</Callout>;
  if (!s) return null;

  const monthBoxes = MONTHS.map((_, m) => {
    const vals = idx.map((ms, i) => (new Date(ms).getUTCMonth() === m ? col("t_db_c")[i] : null)).filter((v): v is number => v !== null);
    return quantiles(vals, [0, 0.25, 0.5, 0.75, 1]);
  });
  const step = Math.max(1, Math.floor(sel.length / 2500));
  const scatterPts = sel.filter((_, k) => k % step === 0).map((i) => [col("t_db_c")[i] as number, col("t_wb_c")[i] as number] as [number, number]);
  const ground = s.monthly_ground_temperatures_epw_modeled;
  const soil = s.soil_layers;
  const inspectRow = inspect ? ts?.index.findIndex((x) => x.startsWith(inspect)) ?? -1 : -1;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[20px] font-semibold">Climate intelligence</h1>
          <p className="text-[12px] text-ink-2">{project.data?.config.site.name} · {sourceText}</p>
        </div>
        <div className="flex gap-2 items-center flex-wrap">
          <label className="text-[12px] text-ink-2 flex items-center gap-1">Period
            <select className="input !w-auto" value={month} onChange={(e) => setMonth(Number(e.target.value))}>
              <option value={0}>Full record</option>
              {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
            </select>
          </label>
          <a className="btn" href={api.url(`/api/climate/${datasetId}/timeseries?format=raw_csv`)}>Raw data CSV</a>
          <a className="btn" href={api.url(`/api/climate/${datasetId}/timeseries?format=csv&elevation_m=${elevation ?? 0}`)}>Processed CSV</a>
        </div>
      </div>
      {s.period.data_nature === "typical_year" && (
        <Callout level="info" title="Typical year">This is a typical meteorological year assembled from representative months of different years ({prov.reference_period?.slice(0, 80)}…). It understates extremes; check design conditions against extreme-year data.</Callout>
      )}
      {qc?.warnings?.map((w: string, i: number) => <Callout key={i} level="warning" title="Data quality">{w}</Callout>)}

      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-3">
        <StatTile label="Mean dry bulb" value={fmt(s.overall.t_db_c?.mean, 1)} unit="°C" sub={`P5 ${fmt(s.overall.t_db_c?.p05, 1)} · P95 ${fmt(s.overall.t_db_c?.p95, 1)}`} />
        <StatTile label="0.4 % design dry bulb" value={fmt(s.design_conditions.t_db_c?.["0.4%"], 1)} unit="°C" sub="exceeded 35 h/yr in this record" />
        <StatTile label="0.4 % design wet bulb" value={fmt(s.design_conditions.t_wb_c?.["0.4%"], 1)} unit="°C" />
        <StatTile label="Hours ≤ 24 °C dry bulb" value={fmt(s.free_cooling_hours_below_threshold.hours)} unit="h" sub={`longest run ${s.free_cooling_hours_below_threshold.longest_run_h} h`} />
        <StatTile label="Hot & humid hours" value={fmt(s.hot_humid_hours.hours)} unit="h" sub={s.hot_humid_hours.definition} />
        <StatTile label="Heatwaves" value={fmt(s.heatwaves?.count)} sub={`longest ${s.heatwaves?.max_duration_days ?? 0} days; ${s.heatwaves?.definition ?? ""}`} />
      </div>

      <Section title="Temperature and humidity">
        <div className="grid xl:grid-cols-2 gap-4">
          <ChartCard id="c1-temperature" number={1} title="Hourly dry-bulb temperature" units="°C" period={month ? MONTHS[month - 1] : period} source={sourceText}
            option={ts ? lineChart(t, { time: true, zoom: true, yName: "°C", unit: "°C", series: [{ name: "Dry bulb", data: sel.map((i) => [idx[i], col("t_db_c")[i]] as [number, number | null]) }] }) : undefined}
            table={ts ? { columns: ["time_utc", "t_db_c"], rows: sel.map((i) => [ts.index[i], col("t_db_c")[i]]) } : undefined}
            empty={series.isLoading ? "Loading series…" : undefined} />
          <ChartCard id="c2-monthly-temp" number={2} title="Monthly temperature distribution" units="°C (min, quartiles, max)" period={period} source={sourceText}
            assumptions="Months in UTC; boxes span the inter-quartile range."
            option={ts ? boxplot(t, { categories: MONTHS, boxes: monthBoxes, unit: "°C", yName: "°C" }) : undefined}
            table={{ columns: ["month", "min", "q1", "median", "q3", "max"], rows: monthBoxes.map((b, i) => [MONTHS[i], ...b]) }} />
          <ChartCard id="c3a-rh" number={3} title="Relative humidity" units="%" period={month ? MONTHS[month - 1] : period} source={sourceText}
            assumptions="Shown separately from dew point (no dual axes)."
            option={ts ? lineChart(t, { time: true, zoom: true, yName: "%", unit: "%", yMin: 0, series: [{ name: "Relative humidity", data: sel.map((i) => [idx[i], col("rh_pct")[i]] as [number, number | null]) }] }) : undefined}
            table={ts ? { columns: ["time_utc", "rh_pct"], rows: sel.map((i) => [ts.index[i], col("rh_pct")[i]]) } : undefined} />
          <ChartCard id="c3b-dewpoint" number={3} title="Dew point" units="°C" period={month ? MONTHS[month - 1] : period} source={sourceText}
            assumptions="Upper recommended limit 15 °C dew point (ASHRAE A1–A4 recommended envelope) marked."
            option={ts ? lineChart(t, { time: true, zoom: true, yName: "°C", unit: "°C", markLines: [{ y: 15, label: "15 °C DP limit" }], series: [{ name: "Dew point", data: sel.map((i) => [idx[i], col("t_dp_c")[i]] as [number, number | null]) }] }) : undefined}
            table={ts ? { columns: ["time_utc", "t_dp_c"], rows: sel.map((i) => [ts.index[i], col("t_dp_c")[i]]) } : undefined} />
          <ChartCard id="c4-wb-db" number={4} title="Wet bulb versus dry bulb" units="°C" period={month ? MONTHS[month - 1] : period} source={`${sourceText}; wet bulb: ASHRAE thermodynamic, site pressure`}
            assumptions={step > 1 ? `One hour in every ${step} plotted for legibility; the table/CSV has all hours.` : undefined}
            option={ts ? scatter(t, { series: [{ name: "Hourly states", data: scatterPts, size: 5 }], xName: "dry bulb (°C)", yName: "wet bulb (°C)", xUnit: "°C db", yUnit: "°C wb" }) : undefined}
            table={ts ? { columns: ["time_utc", "t_db_c", "t_wb_c"], rows: sel.map((i) => [ts.index[i], col("t_db_c")[i], col("t_wb_c")[i]]) } : undefined} />
          <ChartCard id="c-hist-temp" title="Dry-bulb temperature distribution" units="hours per 2 K bin" period={period} source={sourceText}
            option={barChart(t, { categories: s.histograms.t_db_c?.edges.slice(0, -1).map((e) => `${e}`) ?? [], series: [{ name: "hours", data: s.histograms.t_db_c?.counts ?? [] }], yName: "h", unit: "h" })}
            table={{ columns: ["bin_start_c", "hours"], rows: (s.histograms.t_db_c?.edges.slice(0, -1) ?? []).map((e, i) => [e, s.histograms.t_db_c.counts[i]]) }} />
        </div>
      </Section>

      <Section title="Wind, solar and ground">
        <div className="grid xl:grid-cols-3 gap-4">
          <ChartCard id="c5-windrose" number={5} title="Wind rose" units="% of hours by direction and speed class" period={period} source={sourceText}
            assumptions={`Prevailing ${s.wind_rose.prevailing}; calm (< 0.5 m/s) ${fmt(s.wind_rose.calm_pct, 1)} %. 10 m station/grid wind.`}
            option={windRose(t, { sectors: s.wind_rose.sectors, bins: s.wind_rose.speed_bins_ms, freq: s.wind_rose.frequency_pct })} height={320}
            table={{ columns: ["sector", ...s.wind_rose.speed_bins_ms.slice(0, -1).map((b, j) => `${b}-${s.wind_rose.speed_bins_ms[j + 1]} m/s`)], rows: s.wind_rose.sectors.map((sec, i) => [sec, ...s.wind_rose.frequency_pct[i]]) }} />
          <ChartCard id="c6-solar" number={6} title="Solar radiation by month and hour" units="mean GHI, W/m²" period={`${period}; hours in local time`} source={sourceText}
            assumptions={s.solar ? `Annual GHI ≈ ${fmt(s.solar.annual_kwh_m2)} kWh/m².` : undefined}
            empty={!s.solar ? "No solar radiation in this dataset." : undefined}
            option={s.solar ? heatmap(t, { x: s.solar.hours, y: s.solar.months.map((m) => MONTHS[m - 1]), xName: "hour", yName: "", unit: "W/m²", data: s.solar.mean_ghi_wm2.flatMap((r, yi) => r.map((v, xi) => [xi, yi, v] as [number, number, number])) }) : undefined}
            height={320}
            table={s.solar ? { columns: ["month", ...s.solar.hours.map((h) => `h${h}`)], rows: s.solar.mean_ghi_wm2.map((r, i) => [MONTHS[s.solar!.months[i] - 1], ...r]) } : undefined} />
          <ChartCard id="c7-ground" number={7} title="Ground temperature by depth and month" units="°C" period="monthly" height={320}
            source={Object.keys(soil).length ? "Reanalysis soil layers (model layers, not site measurements)" : "EPW header ground temperatures (modeled by the weather converter, not measured)"}
            assumptions="The simulation recalibrates ground temperature with the project's soil properties (Kusuda–Achenbach model)."
            empty={!Object.keys(ground).length && !Object.keys(soil).length ? "No ground-temperature information in this dataset; the simulation derives it from air temperature (documented approximation)." : undefined}
            option={Object.keys(ground).length || Object.keys(soil).length ? lineChart(t, {
              x: MONTHS, yName: "°C", unit: "°C",
              series: Object.keys(ground).length
                ? Object.entries(ground).map(([d, v]) => ({ name: `${d} m`, data: v }))
                : Object.entries(soil).map(([c, v]: [string, any]) => ({ name: `${v.depth_m} m (${c})`, data: v.monthly_mean })),
            }) : undefined}
            table={{ columns: ["depth", ...MONTHS], rows: Object.keys(ground).length ? Object.entries(ground).map(([d, v]) => [d, ...v]) : Object.entries(soil).map(([c, v]: [string, any]) => [c, ...v.monthly_mean]) }} />
        </div>
      </Section>

      <Section title="Grid carbon and water context">
        <div className="grid md:grid-cols-2 gap-4">
          <ChartCard id="c8-grid" number={8} title="Grid carbon intensity" units="kg CO₂e/kWh" source={String(project.data?.config.grid.source ?? "not provided")}
            empty={
              <div className="flex flex-col gap-1 items-center">
                <div className="text-[22px] font-semibold">{project.data?.config.grid.emission_factor_kg_per_kwh ?? "—"} <span className="text-[13px] font-normal">kg CO₂e/kWh</span></div>
                <div>{project.data?.config.grid.emission_factor_kg_per_kwh != null
                  ? `${project.data?.config.grid.factor_type} factor (${project.data?.config.grid.accounting_basis}-based). No hourly series was supplied, so no hourly profile is shown — none is invented.`
                  : "No grid emission factor supplied: carbon results are unavailable. Enter a sourced factor on the Setup page or upload an hourly series."}</div>
              </div>
            } />
          <ChartCard id="c9-water" number={9} title="Water stress and availability" units="WRI Aqueduct baseline water stress (0–5)" source={String(project.data?.config.site.constraints.water_stress_source ?? "not provided")}
            empty={
              <div className="grid grid-cols-2 gap-3 text-left">
                <div><div className="text-ink-2 text-[12px]">Basin water stress</div><div className="text-[20px] font-semibold">{project.data?.config.site.constraints.water_stress_score ?? "not provided"}</div></div>
                <div><div className="text-ink-2 text-[12px]">Permitted withdrawal</div><div className="text-[20px] font-semibold">{project.data?.config.site.constraints.permitted_annual_withdrawal_m3 ?? "no limit entered"}</div></div>
                <div><div className="text-ink-2 text-[12px]">Annual rainfall</div><div className="text-[16px] font-semibold">{s.rainfall ? `${fmt(s.rainfall.annual_mm)} mm` : "not in dataset"}</div></div>
                <div><div className="text-ink-2 text-[12px]">Water source</div><div className="text-[16px] font-semibold">{project.data?.config.site.constraints.water_source}</div></div>
                <p className="col-span-2 text-[11px] text-ink-3">A basin index is context for weighting, not a site withdrawal permit.</p>
              </div>
            } />
        </div>
      </Section>

      <Section title="Provenance, completeness and inspection">
        <div className="grid xl:grid-cols-2 gap-4">
          <div className="card p-4 overflow-auto">
            <table className="w-full text-[12px]">
              <tbody>
                {["provider", "product", "data_nature", "source_url", "endpoint", "retrieved_at", "requested_lat", "requested_lon", "actual_lat", "actual_lon",
                  "actual_elevation_m", "distance_km", "elevation_difference_m", "spatial_resolution", "temporal_resolution", "timezone", "reference_period", "licence", "citation"].map((k) => (
                  <tr key={k} className="border-b border-line align-top"><th className="text-left py-1 pr-2 font-medium text-ink-2 whitespace-nowrap">{k}</th><td className="break-all">{String(prov[k] ?? "—")}</td></tr>
                ))}
                <tr className="border-b border-line align-top"><th className="text-left py-1 pr-2 font-medium text-ink-2">transformations</th><td>{(prov.transformations ?? []).join("; ")}</td></tr>
                <tr className="align-top"><th className="text-left py-1 pr-2 font-medium text-ink-2">notes</th><td>{(prov.notes ?? []).join("; ")}</td></tr>
              </tbody>
            </table>
          </div>
          <div className="flex flex-col gap-4">
            <ChartCard id="c-completeness" title="Data completeness" units="% of hours with a value" period={period} source={sourceText}
              option={barChart(t, { categories: Object.keys(s.completeness_pct), series: [{ name: "completeness", data: Object.values(s.completeness_pct) }], yName: "%", unit: "%", horizontal: true, yMin: 0, labels: true })}
              table={{ columns: ["variable", "completeness_pct"], rows: Object.entries(s.completeness_pct) }} height={Math.max(160, Object.keys(s.completeness_pct).length * 24)} />
            <div className="card p-4 flex flex-col gap-2">
              <label className="label" htmlFor="inspect">Inspect a timestamp (UTC, e.g. 2001-07-05T17)</label>
              <input id="inspect" className="input" value={inspect} onChange={(e) => setInspect(e.target.value)} placeholder="YYYY-MM-DDTHH" />
              {inspect && ts && (inspectRow >= 0 ? (
                <table className="text-[12px] tnum"><tbody>
                  <tr><th className="text-left pr-3">time</th><td>{ts.index[inspectRow]}</td></tr>
                  {Object.keys(ts.columns).map((k) => <tr key={k}><th className="text-left pr-3">{k}</th><td>{fmt(ts.columns[k][inspectRow] as number, 2)}</td></tr>)}
                </tbody></table>
              ) : <p className="text-[12px] text-ink-2">No record at that time.</p>)}
            </div>
          </div>
        </div>
      </Section>
    </div>
  );
}
