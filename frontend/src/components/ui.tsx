import * as RSwitch from "@radix-ui/react-switch";
import * as RSlider from "@radix-ui/react-slider";
import type { ReactNode } from "react";
import type { Job, SourceTag } from "../types/api";

export function StatTile({ label, value, unit, sub, delta, goodWhenDown = true }: {
  label: string; value: string; unit?: string; sub?: ReactNode; delta?: number | null; goodWhenDown?: boolean;
}) {
  const good = delta === null || delta === undefined ? null : goodWhenDown ? delta < 0 : delta > 0;
  return (
    <div className="card p-3 min-w-0" role="group" aria-label={label}>
      <div className="text-[12px] text-ink-2">{label}</div>
      <div className="text-[22px] font-semibold leading-tight mt-0.5">
        {value}
        {unit && <span className="text-[13px] font-normal text-ink-2 ml-1">{unit}</span>}
      </div>
      {delta !== undefined && delta !== null && (
        <div className="text-[12px] mt-0.5 font-medium" style={{ color: good ? "var(--success-text)" : "var(--status-critical)" }}>
          {good ? "▼" : "▲"} {Math.abs(delta).toFixed(1)} % vs baseline
        </div>
      )}
      {sub && <div className="text-[11px] text-ink-3 mt-1 leading-snug">{sub}</div>}
    </div>
  );
}

const STATUS_STYLE: Record<string, { color: string; icon: string; label: string }> = {
  error: { color: "var(--status-critical)", icon: "⛔", label: "Error" },
  conflict: { color: "var(--status-critical)", icon: "⛔", label: "Conflict" },
  warning: { color: "var(--status-serious)", icon: "⚠", label: "Warning" },
  info: { color: "var(--series-1)", icon: "ℹ", label: "Note" },
  good: { color: "var(--status-good)", icon: "✓", label: "OK" },
};

/** Status always carries icon + label, never colour alone. */
export function Callout({ level = "info", title, children }: { level?: string; title?: string; children: ReactNode }) {
  const s = STATUS_STYLE[level] ?? STATUS_STYLE.info;
  return (
    <div className="card px-3 py-2 text-[13px] flex gap-2 items-start" style={{ borderLeft: `3px solid ${s.color}` }} role={level === "error" ? "alert" : "note"}>
      <span aria-hidden>{s.icon}</span>
      <div className="min-w-0">
        <span className="font-semibold">{title ?? s.label}: </span>
        {children}
      </div>
    </div>
  );
}

const SOURCE_LABEL: Record<SourceTag, string> = {
  "user": "user", "default-assumption": "assumed", "measured": "measured", "quoted": "quoted", "derived": "derived", "retrieved": "retrieved",
};

export function SourceBadge({ source }: { source?: SourceTag }) {
  if (!source) return null;
  const assumed = source === "default-assumption";
  return (
    <span
      className="text-[10px] px-1.5 py-[1px] rounded-full border whitespace-nowrap"
      style={{ borderColor: assumed ? "var(--status-serious)" : "var(--baseline)", color: assumed ? "var(--text-primary)" : "var(--text-secondary)" }}
      title={assumed ? "Default assumption — replace with project data" : `Value source: ${source}`}
    >
      {assumed ? "⚠ " : ""}
      {SOURCE_LABEL[source]}
    </span>
  );
}

export function Field({ label, unit, source, children, help }: { label: string; unit?: string; source?: SourceTag; children: ReactNode; help?: string }) {
  return (
    <label className="flex flex-col gap-1 min-w-0">
      <span className="label flex items-center gap-1.5 flex-wrap">
        {label}
        {unit && <span className="text-ink-3">({unit})</span>}
        <SourceBadge source={source} />
      </span>
      {children}
      {help && <span className="text-[11px] text-ink-3">{help}</span>}
    </label>
  );
}

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="flex items-center gap-2 text-[13px] cursor-pointer">
      <RSwitch.Root checked={checked} onCheckedChange={onChange} aria-label={label}
        className="w-9 h-5 rounded-full relative border border-axis data-[state=checked]:bg-[var(--seq-500)] bg-[var(--surface-2)]">
        <RSwitch.Thumb className="block w-4 h-4 rounded-full bg-white shadow translate-x-0.5 data-[state=checked]:translate-x-[18px] transition-transform" />
      </RSwitch.Root>
      {label}
    </label>
  );
}

export function Slider({ label, value, min, max, step, unit, onChange }: {
  label: string; value: number; min: number; max: number; step: number; unit?: string; onChange: (v: number) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between text-[12px]">
        <span className="text-ink-2">{label}</span>
        <span className="font-semibold tnum">{value}{unit ? ` ${unit}` : ""}</span>
      </div>
      <RSlider.Root className="relative flex items-center h-5 select-none touch-none" value={[value]} min={min} max={max} step={step}
        onValueChange={(v) => onChange(v[0])}>
        <RSlider.Track className="relative grow h-[3px] rounded-full bg-[var(--seq-100)]">
          <RSlider.Range className="absolute h-full rounded-full bg-[var(--seq-500)]" />
        </RSlider.Track>
        <RSlider.Thumb aria-label={label} className="block w-4 h-4 rounded-full bg-white border-2 border-[var(--seq-500)] shadow focus-visible:outline-2" />
      </RSlider.Root>
    </div>
  );
}

export function JobBar({ job, label }: { job?: Job; label: string }) {
  if (!job) return null;
  const pct = Math.round((job.progress ?? 0) * 100);
  const failed = job.status === "failed" || job.status === "cancelled";
  return (
    <div className="card px-3 py-2 text-[12px]" role="status" aria-live="polite">
      <div className="flex justify-between gap-2">
        <span className="font-semibold">{label}</span>
        <span className="text-ink-2">{job.status === "running" || job.status === "queued" ? `${pct} %` : job.status}</span>
      </div>
      <div className="h-[3px] rounded-full bg-[var(--seq-100)] mt-1.5 overflow-hidden">
        <div className="h-full" style={{ width: `${failed ? 100 : pct}%`, background: failed ? "var(--status-critical)" : "var(--seq-500)" }} />
      </div>
      <div className="text-ink-3 mt-1 truncate">{failed ? job.error?.split("\n").slice(-2).join(" ") || job.message : job.message}</div>
    </div>
  );
}

export function Section({ title, children, right, id }: { title: string; children: ReactNode; right?: ReactNode; id?: string }) {
  return (
    <section className="flex flex-col gap-3" id={id}>
      <div className="flex items-end justify-between gap-2">
        <h2 className="text-[16px] font-semibold">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="card p-8 text-center flex flex-col items-center gap-2">
      <div className="text-[15px] font-semibold">{title}</div>
      <div className="text-[13px] text-ink-2 max-w-xl">{children}</div>
    </div>
  );
}
