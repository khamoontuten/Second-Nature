export const fmt = (v: number | null | undefined, digits = 0): string => {
  if (v === null || v === undefined || !Number.isFinite(v)) return "n/a";
  // values that round to zero print as "0", never "-0"
  const x = Math.abs(v) < 0.5 * 10 ** -digits ? 0 : v;
  return x.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
};

/** Readable label for a snake_case key with a unit suffix, e.g. chimney_height_m → "chimney height (m)". */
export const humanKey = (k: string): string =>
  k.replace(/_m2$/, " (m²)").replace(/_m3s$/, " (m³/s)").replace(/_m$/, " (m)").replace(/_kwh_th$/, " (kWh_th)").replace(/_kw$/, " (kW)").replace(/_pf$/, " (projection factor)").replace(/_/g, " ");

/** Compact display of a scalar, list or flat object value. */
export const showValue = (v: unknown): string => {
  if (typeof v === "number") return fmt(v, Number.isInteger(v) ? 0 : 2);
  if (Array.isArray(v)) return v.map(showValue).join(", ");
  if (v && typeof v === "object") return Object.entries(v).map(([k, x]) => `${k} ${showValue(x)}`).join(", ");
  return String(v ?? "");
};

export const compact = (v: number | null | undefined): string => {
  if (v === null || v === undefined || !Number.isFinite(v)) return "n/a";
  const a = Math.abs(v);
  if (a >= 1e6) return `${(v / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(a >= 1e4 ? 0 : 1)}K`;
  return a >= 10 ? v.toFixed(0) : v.toFixed(a >= 1 ? 1 : 2);
};

export const pct = (v: number | null | undefined, digits = 1): string =>
  v === null || v === undefined || !Number.isFinite(v) ? "n/a" : `${v > 0 ? "+" : ""}${v.toFixed(digits)} %`;

export const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export const MODE_LABELS: Record<string, string> = {
  mechanical: "Mechanical (closed loop)",
  mech_precool: "Mechanical + evaporative pre-cooling",
  econ_direct_fan: "Outdoor-air economiser (fans)",
  econ_natural: "Stack/wind-assisted economiser",
  econ_eahe: "Earth-air pre-cooled intake",
  econ_indirect: "Indirect air economiser",
  iec_wet: "Indirect evaporative",
  dec_direct: "Direct evaporative",
};
export const MODES = Object.keys(MODE_LABELS);

export const SCENARIO_SHORT: Record<string, string> = {
  S0: "Baseline", S1: "Improved", S2: "Envelope", S3: "Stack", S4: "Earth-air", S5: "Facade", S6: "Low-water", S7: "Hybrid", S8: "User",
};

export function downloadCsv(filename: string, header: string[], rows: (string | number | boolean | null | undefined)[][]) {
  const esc = (x: unknown) => {
    const s = x === null || x === undefined ? "" : String(x);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const text = [header.map(esc).join(","), ...rows.map((r) => r.map(esc).join(","))].join("\n");
  const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

export function quantiles(values: number[], qs: number[]): number[] {
  const v = values.filter(Number.isFinite).slice().sort((a, b) => a - b);
  if (!v.length) return qs.map(() => NaN);
  return qs.map((q) => {
    const pos = (v.length - 1) * q;
    const lo = Math.floor(pos);
    const hi = Math.ceil(pos);
    return v[lo] + (v[hi] - v[lo]) * (pos - lo);
  });
}
