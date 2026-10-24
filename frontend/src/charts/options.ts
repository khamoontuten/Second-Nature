/**
 * ECharts option builders implementing the chart conventions:
 * thin marks (bars ≤ 24 px with 4 px rounded data-ends, 2 px lines, ~10 % area wash), hairline solid grid,
 * one y-axis per chart, legend for ≥ 2 series, crosshair tooltip on lines / per-mark tooltip on bars & cells,
 * fixed categorical order (colour follows the entity), one-hue sequential ramp, neutral-midpoint diverging.
 */
import type { EChartsOption } from "echarts";
import { token } from "../lib/theme";

export interface Tokens {
  surface: string;
  text: string;
  text2: string;
  muted: string;
  grid: string;
  axis: string;
  series: string[];
  seq: string[];
  divNeg: string;
  divMid: string;
  divPos: string;
  neutral: string;
}

export function readTokens(): Tokens {
  return {
    surface: token("--surface-1") || "#fcfcfb",
    text: token("--text-primary") || "#0b0b0b",
    text2: token("--text-secondary") || "#52514e",
    muted: token("--text-muted") || "#6f6d68",
    grid: token("--gridline") || "#e1e0d9",
    axis: token("--baseline") || "#c3c2b7",
    series: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => token(`--series-${i}`)),
    seq: [100, 200, 300, 400, 500, 600, 700].map((s) => token(`--seq-${s}`)),
    divNeg: token("--div-neg"),
    divMid: token("--div-mid"),
    divPos: token("--div-pos"),
    neutral: token("--series-neutral") || "#8f8d84",
  };
}

const esc = (s: unknown) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

export const num = (v: unknown, d = 1) => {
  const x = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(x)) return "n/a";
  // small magnitudes keep two significant digits instead of rounding to "0"
  if (x !== 0 && Math.abs(x) < 10 ** -d) return x.toLocaleString(undefined, { maximumSignificantDigits: 2 });
  return x.toLocaleString(undefined, { maximumFractionDigits: Math.abs(x) >= 100 ? 0 : d });
};

const axisLabel = (t: Tokens) => ({ color: t.muted, fontSize: 11, fontFamily: "system-ui, sans-serif" });
const tickFmt = (v: number) => {
  const a = Math.abs(v);
  if (a >= 1e6) return `${+(v / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${+(v / 1e3).toFixed(1)}K`;
  if (a > 0 && a < 0.01) return `${+v.toPrecision(2)}`;
  return `${+v.toFixed(2)}`;
};

/** Plot-area top: room for the legend row (if any), then the y-axis name, then value labels above the tallest mark. */
const top = (legend: boolean) => (legend ? 66 : 42);
// ECharts has no built-in "line" legend icon; a 14 × 2 path draws the line key used for line series.
const LINE_ICON = "path://M0,0H14V2H0Z";

function base(t: Tokens, opts: { legend?: boolean; legendIcon?: string } = {}): EChartsOption {
  return {
    backgroundColor: "transparent",
    textStyle: { fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif", color: t.text2 },
    animationDuration: 250,
    legend: opts.legend
      ? { type: "scroll", top: 0, left: 0, right: 0, icon: opts.legendIcon === "line" ? LINE_ICON : opts.legendIcon ?? "roundRect", itemWidth: 14,
          itemHeight: opts.legendIcon === "line" ? 2 : 10, textStyle: { color: t.text2, fontSize: 12 }, pageTextStyle: { color: t.muted }, pageIconColor: t.text2,
          // markers carry a surface-coloured ring; without this the ring is inherited by the 2 px legend key and hides it
          itemStyle: { borderWidth: 0 } }
      : undefined,
    tooltip: {
      backgroundColor: t.surface,
      borderColor: t.axis,
      borderWidth: 1,
      textStyle: { color: t.text, fontSize: 12 },
      extraCssText: "box-shadow:0 4px 14px rgba(0,0,0,.12);border-radius:8px;",
    },
  };
}

/** Tooltip row: value first (strong), series name second, keyed by a short line in the series colour. */
const row = (color: string, name: string, value: string) =>
  `<div style="display:flex;align-items:center;gap:8px;line-height:18px"><span style="display:inline-block;width:12px;height:2px;background:${color}"></span><b>${esc(value)}</b><span style="opacity:.75">${esc(name)}</span></div>`;

const yAxis = (t: Tokens, name: string, extra: Record<string, unknown> = {}) => ({
  type: "value" as const,
  name,
  nameGap: 24,
  nameTextStyle: { color: t.muted, fontSize: 11, align: "left" as const, padding: [0, 0, 0, -36] },
  axisLabel: { ...axisLabel(t), formatter: tickFmt },
  splitLine: { lineStyle: { color: t.grid, width: 1, type: "solid" as const } },
  axisLine: { show: false },
  ...extra,
});

const catAxis = (t: Tokens, data: (string | number)[], extra: Record<string, unknown> = {}) => ({
  type: "category" as const,
  data,
  axisLabel: axisLabel(t),
  axisTick: { show: false },
  axisLine: { lineStyle: { color: t.axis } },
  ...extra,
});

export interface LineSeries {
  name: string;
  data: (number | null)[] | (string | number | null)[][];
  color?: string;
  area?: boolean;
}

export function lineChart(t: Tokens, o: { x?: (string | number)[]; series: LineSeries[]; yName: string; unit: string; time?: boolean; zoom?: boolean; yMin?: number | "dataMin"; markLines?: { y: number; label: string }[]; xValue?: { name: string; unit: string; min?: number; max?: number } }): EChartsOption {
  const multi = o.series.length > 1;
  return {
    ...base(t, { legend: multi, legendIcon: "line" }),
    grid: { left: 52, right: 24, top: top(multi), bottom: o.zoom ? 64 : o.xValue ? 44 : 28, containLabel: false },
    tooltip: {
      ...(base(t).tooltip as object),
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { color: t.axis, width: 1 } },
      formatter: (ps: any) => {
        const arr = Array.isArray(ps) ? ps : [ps];
        const head = esc(o.xValue ? `${o.xValue.name}: ${num(arr[0]?.axisValue, 2)}` : arr[0]?.axisValueLabel ?? "");
        return `<div style="font-size:11px;opacity:.7;margin-bottom:2px">${head}</div>` +
          arr.map((p: any) => row(p.color, p.seriesName, `${num(Array.isArray(p.value) ? p.value[1] : p.value, 2)} ${o.unit}`)).join("");
      },
    },
    xAxis: o.time
      ? { type: "time", axisLabel: axisLabel(t), axisLine: { lineStyle: { color: t.axis } }, splitLine: { show: false } }
      : o.xValue
        ? { type: "value", name: o.xValue.name, nameLocation: "middle", nameGap: 26, nameTextStyle: { color: t.muted, fontSize: 11 }, min: o.xValue.min, max: o.xValue.max,
            axisLabel: { ...axisLabel(t), formatter: tickFmt }, axisLine: { lineStyle: { color: t.axis } }, splitLine: { show: false } }
        : catAxis(t, o.x ?? [], { boundaryGap: false }),
    yAxis: yAxis(t, o.yName, o.yMin !== undefined ? { min: o.yMin } : { scale: true }),
    dataZoom: o.zoom ? [{ type: "inside" }, { type: "slider", height: 18, bottom: 8, borderColor: t.axis, textStyle: { color: t.muted } }] : undefined,
    series: o.series.map((s, i) => ({
      type: "line",
      name: s.name,
      data: s.data as any,
      showSymbol: false,
      symbolSize: 8,
      sampling: "lttb",
      lineStyle: { width: 2, color: s.color ?? t.series[i] },
      itemStyle: { color: s.color ?? t.series[i], borderColor: t.surface, borderWidth: 2 },
      areaStyle: s.area ? { color: s.color ?? t.series[i], opacity: 0.1 } : undefined,
      emphasis: { focus: "series" },
      markLine: i === 0 && o.markLines?.length
        ? { silent: true, symbol: "none", lineStyle: { color: t.text2, width: 1, type: "solid" }, label: { color: t.text2, fontSize: 11, formatter: "{b}", position: "insideEndTop" },
            data: o.markLines.map((m) => ({ yAxis: m.y, name: m.label })) }
        : undefined,
    })),
  };
}

export interface BarSeries {
  name: string;
  data: (number | null)[];
  color?: string;
  colors?: string[]; // per-bar colours (emphasis pattern, e.g. baseline in neutral)
  stack?: string;
}

export function barChart(t: Tokens, o: { categories: (string | number)[]; series: BarSeries[]; yName: string; unit: string; horizontal?: boolean; labels?: boolean; legend?: boolean; yMin?: number }): EChartsOption {
  const multi = o.series.length > 1;
  const stacked = o.series.some((s) => s.stack);
  const radius = (v: number | null) => {
    if (stacked) return 0;
    const neg = (v ?? 0) < 0;
    return o.horizontal ? (neg ? [4, 0, 0, 4] : [0, 4, 4, 0]) : neg ? [0, 0, 4, 4] : [4, 4, 0, 0];
  };
  const valueAxis = o.horizontal
    ? { ...yAxis(t, o.yName, o.yMin !== undefined ? { min: o.yMin } : {}), nameLocation: "middle", nameGap: 26, nameTextStyle: { color: t.muted, fontSize: 11 } }
    : yAxis(t, o.yName, o.yMin !== undefined ? { min: o.yMin } : {});
  const ca = catAxis(t, o.categories, o.horizontal ? { inverse: true } : { axisLabel: { ...axisLabel(t), interval: o.categories.length > 12 ? "auto" : 0, hideOverlap: true } });
  const legend = o.legend ?? multi;
  return {
    ...base(t, { legend }),
    // horizontal bars: category labels sized to fit (containLabel), value labels need room on the right
    grid: o.horizontal
      ? { left: 8, right: o.labels ? 64 : 24, top: legend ? 36 : 12, bottom: 40, containLabel: true }
      : { left: 56, right: 24, top: top(legend), bottom: 28 },
    tooltip: {
      ...(base(t).tooltip as object),
      trigger: "item",
      formatter: (p: any) =>
        `<div style="font-size:11px;opacity:.7;margin-bottom:2px">${esc(p.name)}</div>` + row(p.color, p.seriesName, `${num(p.value, 2)} ${o.unit}`),
    },
    xAxis: o.horizontal ? (valueAxis as any) : (ca as any),
    yAxis: o.horizontal ? (ca as any) : (valueAxis as any),
    series: o.series.map((s, i) => ({
      type: "bar",
      name: s.name,
      stack: s.stack,
      barMaxWidth: 24,
      barGap: "15%",
      barCategoryGap: "35%",
      // series-level colour drives the legend swatch; per-bar colours (e.g. baseline in neutral) override it on the marks
      itemStyle: { color: s.color ?? t.series[i] },
      data: s.data.map((v, j) => ({
        value: v,
        itemStyle: { color: s.colors?.[j] ?? s.color ?? t.series[i], borderRadius: radius(v), borderColor: stacked ? t.surface : undefined, borderWidth: stacked ? 1 : 0 },
      })),
      emphasis: { itemStyle: { opacity: 0.85 } },
      label: o.labels
        ? { show: true, position: o.horizontal ? "right" : "top", color: t.text2, fontSize: 11, formatter: (p: any) => (p.value === null ? "" : num(p.value, 2)) }
        : undefined,
      labelLayout: o.labels ? { hideOverlap: true } : undefined,
    })),
  };
}

export function heatmap(t: Tokens, o: { x: (string | number)[]; y: (string | number)[]; data: [number, number, number | null][]; unit: string; xName?: string; yName?: string; min?: number; max?: number; pieces?: { value: number; label: string; color: string }[] }): EChartsOption {
  const vals = o.data.map((d) => d[2]).filter((v): v is number => v !== null && Number.isFinite(v));
  return {
    ...base(t),
    grid: { left: 52, right: 16, top: o.yName ? 28 : 12, bottom: o.pieces ? 72 : 56 },
    tooltip: {
      ...(base(t).tooltip as object),
      trigger: "item",
      formatter: (p: any) => {
        const v = p.value?.[2];
        const label = o.pieces ? o.pieces.find((pc) => pc.value === v)?.label ?? "" : `${num(v, 1)} ${o.unit}`;
        return `<div style="font-size:11px;opacity:.7">${esc(o.xName ?? "")} ${esc(o.x[p.value[0]])} · ${esc(o.yName ?? "")} ${esc(o.y[p.value[1]])}</div><b>${esc(label)}</b>`;
      },
    },
    xAxis: catAxis(t, o.x, { name: o.xName, nameLocation: "middle", nameGap: 22, nameTextStyle: { color: t.muted, fontSize: 11 }, splitArea: { show: false } }),
    yAxis: catAxis(t, o.y, { name: o.yName, nameTextStyle: { color: t.muted, fontSize: 11 } }),
    visualMap: o.pieces
      ? { type: "piecewise", orient: "horizontal", left: 0, bottom: 0, itemWidth: 12, itemHeight: 10, textStyle: { color: t.text2, fontSize: 11 },
          pieces: o.pieces.map((p) => ({ value: p.value, label: p.label, color: p.color })) }
      : { type: "continuous", orient: "horizontal", left: 0, bottom: 0, itemWidth: 10, itemHeight: 140, calculable: false,
          min: o.min ?? Math.min(...vals), max: o.max ?? Math.max(...vals), inRange: { color: t.seq }, textStyle: { color: t.text2, fontSize: 11 },
          text: [`${num(o.max ?? Math.max(...vals), 1)} ${o.unit}`, `${num(o.min ?? Math.min(...vals), 1)} ${o.unit}`], textGap: 6 },
    series: [{ type: "heatmap", data: o.data as any, progressive: 4000, itemStyle: { borderColor: t.surface, borderWidth: 0.5 }, emphasis: { itemStyle: { borderColor: t.text, borderWidth: 1 } } }],
  };
}

export function windRose(t: Tokens, o: { sectors: string[]; bins: number[]; freq: number[][] }): EChartsOption {
  const nb = o.bins.length - 1;
  const labels = Array.from({ length: nb }, (_, j) => (j === nb - 1 ? `≥ ${o.bins[j]} m/s` : `${o.bins[j]}–${o.bins[j + 1]} m/s`));
  // magnitude classes → one-hue sequential ramp (ordinal; lightest step kept ≥ 2:1 on the surface)
  const ramp = t.seq.slice(1).concat(t.seq.slice(-1));
  return {
    ...base(t, { legend: true }),
    legend: { top: 0, left: 0, data: labels, icon: "roundRect", itemWidth: 12, itemHeight: 10, textStyle: { color: t.text2, fontSize: 11 }, itemStyle: { borderWidth: 0 } },
    tooltip: { ...(base(t).tooltip as object), trigger: "item", formatter: (p: any) => `<div style="font-size:11px;opacity:.7">${esc(p.name)} · ${esc(p.seriesName)}</div><b>${num(p.value, 2)} % of hours</b>` },
    polar: { radius: ["6%", "70%"], center: ["50%", "58%"] },
    angleAxis: { type: "category", data: o.sectors, startAngle: 90 + 360 / o.sectors.length / 2, axisLabel: axisLabel(t), axisLine: { lineStyle: { color: t.axis } } },
    radiusAxis: { axisLabel: { ...axisLabel(t), formatter: "{value} %" }, splitLine: { lineStyle: { color: t.grid } }, axisLine: { show: false } },
    series: labels.map((name, j) => ({
      type: "bar", coordinateSystem: "polar", stack: "speed", name, data: o.freq.map((row) => row[j]),
      itemStyle: { color: ramp[Math.min(j, ramp.length - 1)], borderColor: t.surface, borderWidth: 1 },
    })),
  };
}

export function scatter(t: Tokens, o: { series: { name: string; data: [number, number, ...unknown[]][]; color?: string; size?: number }[]; xName: string; yName: string; xUnit: string; yUnit: string; tooltipExtra?: (d: unknown[]) => string }): EChartsOption {
  // all-pairs palette rule: at most three categorical series on a scatter
  const series = o.series.slice(0, 3);
  return {
    ...base(t, { legend: series.length > 1 }),
    grid: { left: 60, right: 20, top: top(series.length > 1), bottom: 44 },
    tooltip: {
      ...(base(t).tooltip as object),
      trigger: "item",
      formatter: (p: any) => `${row(p.color, p.seriesName, `${num(p.value[0], 2)} ${o.xUnit}, ${num(p.value[1], 2)} ${o.yUnit}`)}${o.tooltipExtra ? o.tooltipExtra(p.value) : ""}`,
    },
    // value-vs-value axes sit on the plot edge, not at zero (otherwise they cut through the data)
    xAxis: { type: "value", name: o.xName, nameLocation: "middle", nameGap: 26, nameTextStyle: { color: t.muted, fontSize: 11 }, scale: true,
             axisLabel: { ...axisLabel(t), formatter: tickFmt }, splitLine: { lineStyle: { color: t.grid } }, axisLine: { onZero: false, lineStyle: { color: t.axis } } },
    yAxis: yAxis(t, o.yName, { scale: true, axisLine: { show: false, onZero: false } }),
    series: series.map((s, i) => ({
      type: "scatter", name: s.name, data: s.data as any, symbolSize: s.size ?? 9, large: s.data.length > 2000,
      itemStyle: { color: s.color ?? t.series[i], borderColor: t.surface, borderWidth: 2, opacity: s.data.length > 500 ? 0.55 : 1 },
      emphasis: { scale: 1.4 },
    })),
  };
}

export function boxplot(t: Tokens, o: { categories: string[]; boxes: number[][]; unit: string; yName: string }): EChartsOption {
  return {
    ...base(t),
    grid: { left: 52, right: 16, top: top(false), bottom: 28 },
    tooltip: {
      ...(base(t).tooltip as object),
      trigger: "item",
      formatter: (p: any) => {
        const [, mn, q1, md, q3, mx] = p.value;
        return `<div style="font-size:11px;opacity:.7">${esc(p.name)}</div><b>median ${num(md, 1)} ${o.unit}</b><br/>IQR ${num(q1, 1)}–${num(q3, 1)} · range ${num(mn, 1)}–${num(mx, 1)}`;
      },
    },
    xAxis: catAxis(t, o.categories),
    yAxis: yAxis(t, o.yName, { scale: true }),
    series: [{ type: "boxplot", data: o.boxes, boxWidth: [8, 20], itemStyle: { color: t.surface, borderColor: t.series[0], borderWidth: 1.5 } }],
  };
}

export function tornado(t: Tokens, o: { rows: { label: string; low: number; high: number }[]; center: number; unit: string; xName: string }): EChartsOption {
  const rows = o.rows.slice(0, 12);
  return {
    ...base(t, { legend: true }),
    grid: { left: 190, right: 30, top: 36, bottom: 36 },
    tooltip: {
      ...(base(t).tooltip as object),
      trigger: "item",
      formatter: (p: any) => {
        const r = rows[p.dataIndex];
        return `<div style="font-size:11px;opacity:.7">${esc(r.label)}</div>${row(t.divNeg, "input at low value", `${num(r.low, 1)} ${o.unit}`)}${row(t.divPos, "input at high value", `${num(r.high, 1)} ${o.unit}`)}`;
      },
    },
    xAxis: { type: "value", name: o.xName, nameLocation: "middle", nameGap: 24, nameTextStyle: { color: t.muted, fontSize: 11 }, scale: true,
             axisLabel: { ...axisLabel(t), formatter: tickFmt }, splitLine: { lineStyle: { color: t.grid } } },
    yAxis: catAxis(t, rows.map((r) => r.label), { inverse: true, axisLabel: { ...axisLabel(t), width: 180, overflow: "truncate" } }),
    series: [
      { type: "bar", name: "input at low value", stack: "t", barMaxWidth: 18, data: rows.map((r) => ({ value: r.low - o.center, itemStyle: { color: t.divNeg } })),
        markLine: { silent: true, symbol: "none", data: [{ xAxis: 0 }], lineStyle: { color: t.text2, width: 1, type: "solid" }, label: { show: false } } },
      { type: "bar", name: "input at high value", stack: "t2", barMaxWidth: 18, barGap: "-100%", data: rows.map((r) => ({ value: r.high - o.center, itemStyle: { color: t.divPos } })) },
    ].map((s) => ({ ...s, label: { show: false } })) as any,
    // values are plotted relative to the central estimate; the axis shows the deviation
  };
}

export function intervals(t: Tokens, o: { categories: string[]; p5: number[]; p50: number[]; p95: number[]; unit: string; yName: string }): EChartsOption {
  return {
    ...base(t),
    grid: { left: 60, right: 20, top: top(false), bottom: 28 },
    tooltip: {
      ...(base(t).tooltip as object),
      trigger: "item",
      formatter: (p: any) => {
        const i = p.dataIndex;
        return `<div style="font-size:11px;opacity:.7">${esc(o.categories[i])}</div><b>${num(o.p50[i], 1)} ${o.unit}</b> median<br/>P5 ${num(o.p5[i], 1)} – P95 ${num(o.p95[i], 1)}`;
      },
    },
    xAxis: catAxis(t, o.categories),
    yAxis: yAxis(t, o.yName, { scale: true }),
    series: [
      { type: "custom", renderItem: (_: any, api: any) => {
          const x = api.coord([api.value(0), 0])[0];
          const lo = api.coord([api.value(0), api.value(1)])[1];
          const hi = api.coord([api.value(0), api.value(2)])[1];
          return { type: "group", children: [
            { type: "line", shape: { x1: x, y1: lo, x2: x, y2: hi }, style: { stroke: t.series[0], lineWidth: 2 } },
            { type: "line", shape: { x1: x - 8, y1: lo, x2: x + 8, y2: lo }, style: { stroke: t.series[0], lineWidth: 2 } },
            { type: "line", shape: { x1: x - 8, y1: hi, x2: x + 8, y2: hi }, style: { stroke: t.series[0], lineWidth: 2 } },
          ] };
        },
        data: o.categories.map((_, i) => [i, o.p5[i], o.p95[i]]) },
      { type: "scatter", data: o.categories.map((_, i) => [i, o.p50[i]]), symbolSize: 10, itemStyle: { color: t.series[0], borderColor: t.surface, borderWidth: 2 } },
    ] as any,
  };
}
