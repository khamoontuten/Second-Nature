import type { ImpactMetric, MetricStatus } from "../types/api";

/** How each value was obtained. Icon + label, so status is never carried by colour alone. */
export const STATUS_META: Record<MetricStatus, { icon: string; label: string; help: string; color: string }> = {
  modelled: { icon: "●", label: "Modelled", help: "Computed by an implemented physics or plant model (hourly simulation).", color: "var(--text-secondary)" },
  derived: { icon: "◆", label: "Derived", help: "Calculated from modelled outputs (ratios, tariffs × energy, factors × energy).", color: "var(--text-secondary)" },
  estimated: { icon: "≈", label: "Estimated", help: "Quantity take-off × unit rates, or layout planning ratios; shown with a range where available.", color: "var(--text-secondary)" },
  insufficient_data: { icon: "?", label: "Insufficient data", help: "The model supports this metric but a required input is missing; no value is substituted.", color: "var(--status-serious)" },
  not_modelled: { icon: "∅", label: "Not modelled", help: "The application cannot determine this quantity.", color: "var(--text-muted)" },
  not_applicable: { icon: "–", label: "Not applicable", help: "The metric does not apply to this configuration.", color: "var(--text-muted)" },
  infeasible: { icon: "⛔", label: "Infeasible", help: "The scenario violates a hard engineering constraint and is not ranked.", color: "var(--status-critical)" },
};

export function StatusBadge({ status, compact }: { status: MetricStatus; compact?: boolean }) {
  const m = STATUS_META[status] ?? STATUS_META.not_modelled;
  return (
    <span className="inline-flex items-center gap-1 text-[10.5px] px-1.5 py-[1px] rounded-full border whitespace-nowrap"
      style={{ borderColor: m.color, color: status === "insufficient_data" || status === "infeasible" ? "var(--text-primary)" : m.color }}
      title={m.help} data-status={status}>
      <span aria-hidden>{m.icon}</span>{compact ? <span className="sr-only">{m.label}</span> : m.label}
    </span>
  );
}

export function StatusLegend() {
  return (
    <div className="flex flex-wrap gap-2 items-center text-[11px] text-ink-2" aria-label="Value status legend">
      {(Object.keys(STATUS_META) as MetricStatus[]).map((s) => <span key={s} title={STATUS_META[s].help}><StatusBadge status={s} /></span>)}
    </div>
  );
}

const MONEY = /^(USD|EUR|GBP|INR|JPY|CNY|AUD|CAD|CHF|SGD|[A-Z]{3})(\/yr)?$/;

/** Number formatting by unit (no false precision). */
export function fmtVal(v: number | null | undefined, unit: string): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  const z = a < 1e-9 ? 0 : v;
  if (MONEY.test(unit)) {
    if (a >= 1e9) return `${(z / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `${(z / 1e6).toFixed(2)}M`;
    if (a >= 1e3) return `${(z / 1e3).toFixed(0)}k`;
    return z.toFixed(0);
  }
  if (unit === "–") return z.toFixed(3);
  if (unit.startsWith("L/kWh")) return a > 0 && a < 0.01 ? z.toPrecision(2) : z.toFixed(3);
  if (unit.includes("°C")) return z.toFixed(1);
  if (a >= 100) return Math.round(z).toLocaleString();
  if (a >= 10) return z.toFixed(1);
  if (a === 0) return "0";
  return z.toFixed(a < 1 ? 3 : 2);
}

export function fmtPct(p: number | null | undefined): string {
  if (p === null || p === undefined || !Number.isFinite(p)) return "";
  const z = Math.abs(p) < 0.05 ? 0 : p;
  return `${z > 0 ? "+" : ""}${z.toFixed(Math.abs(z) < 10 ? 1 : 0)} %`;
}

/** Baseline-relative change: arrow + signed value + unit (+ %), the assessment in words, and the status. */
export function DeltaText({ m, showStatus = true, showPct = true }: { m?: ImpactMetric; showStatus?: boolean; showPct?: boolean }) {
  if (!m) return <span className="text-ink-3">—</span>;
  if (m.delta === null) {
    return (
      <span className="inline-flex items-center gap-1 text-ink-3" title={m.note || m.pct_note || ""}>
        — {showStatus && <StatusBadge status={m.status} compact />}
      </span>
    );
  }
  const arrow = m.direction === "decrease" ? "▼" : m.direction === "increase" ? "▲" : "=";
  const sign = m.delta > 0 ? "+" : "";
  const good = m.assessment === "improvement";
  const bad = m.assessment === "trade-off";
  const range = m.delta_range && Math.abs(m.delta_range[1] - m.delta_range[0]) > 1e-9 ? ` (${fmtVal(m.delta_range[0], m.unit)} … ${fmtVal(m.delta_range[1], m.unit)})` : "";
  return (
    <span className="inline-flex items-center gap-1 flex-wrap tnum" title={`${m.note ?? ""}${m.pct_note ? ` — ${m.pct_note}` : ""}`}>
      <span aria-hidden style={{ color: good ? "var(--success-text)" : bad ? "var(--status-critical)" : "var(--text-muted)" }}>{arrow}</span>
      <span className="font-medium">{sign}{fmtVal(m.delta, m.unit)}</span>
      <span className="text-ink-3">{m.unit}{range}</span>
      {showPct && m.pct !== null && <span className="text-ink-2">({fmtPct(m.pct)})</span>}
      {showPct && m.pct === null && m.pct_note && m.delta !== 0 && <span className="text-ink-3" title={m.pct_note}>(% n/a)</span>}
      <span className="sr-only">{good ? "improvement" : bad ? "trade-off" : "no change"}</span>
      {showStatus && <StatusBadge status={m.status} compact />}
    </span>
  );
}

export const byKey = (metrics?: ImpactMetric[]) => Object.fromEntries((metrics ?? []).map((m) => [m.key, m])) as Record<string, ImpactMetric>;
