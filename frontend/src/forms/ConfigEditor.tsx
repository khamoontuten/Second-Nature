import { createContext, useContext, type ReactNode } from "react";
import type { Assumption } from "../types/api";
import { Field } from "../components/ui";

export function getPath(obj: any, path: string) {
  return path.split(".").reduce((o, k) => (o == null ? o : o[k]), obj);
}

export function setPath(obj: any, path: string, value: unknown) {
  const keys = path.split(".");
  const copy = structuredClone(obj);
  let cur = copy;
  for (const k of keys.slice(0, -1)) {
    cur[k] = cur[k] ?? {};
    cur = cur[k];
  }
  cur[keys[keys.length - 1]] = value;
  return copy;
}

/** Nested patch containing only leaves that differ from the original. */
export function diff(orig: any, draft: any): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(draft)) {
    if (k === "sources") continue;
    const a = orig?.[k];
    const b = draft[k];
    if (b && typeof b === "object" && !Array.isArray(b) && a && typeof a === "object" && !k.endsWith("_per_facade")) {
      const d = diff(a, b);
      if (Object.keys(d).length) out[k] = d;
    } else if (JSON.stringify(a) !== JSON.stringify(b)) {
      out[k] = b;
    }
  }
  return out;
}

interface Ctx {
  draft: any;
  set: (path: string, v: unknown) => void;
  sources: Record<string, Assumption>;
}
export const EditorCtx = createContext<Ctx>({ draft: {}, set: () => {}, sources: {} });

export function NumField({ path, label, unit, min, max, step = "any", help, nullable }: {
  path: string; label: string; unit?: string; min?: number; max?: number; step?: number | "any"; help?: string; nullable?: boolean;
}) {
  const { draft, set, sources } = useContext(EditorCtx);
  const v = getPath(draft, path);
  const outOfRange = typeof v === "number" && ((min !== undefined && v < min) || (max !== undefined && v > max));
  return (
    <Field label={label} unit={unit} source={sources[path]?.source} help={help ?? sources[path]?.description}>
      <input className="input tnum" type="number" value={v ?? ""} min={min} max={max} step={step} aria-invalid={outOfRange}
        style={outOfRange ? { borderColor: "var(--status-critical)" } : undefined}
        onChange={(e) => set(path, e.target.value === "" ? (nullable ? null : 0) : Number(e.target.value))} />
    </Field>
  );
}

export function TextField({ path, label, help }: { path: string; label: string; help?: string }) {
  const { draft, set, sources } = useContext(EditorCtx);
  return (
    <Field label={label} source={sources[path]?.source} help={help}>
      <input className="input" value={getPath(draft, path) ?? ""} onChange={(e) => set(path, e.target.value || null)} />
    </Field>
  );
}

export function SelectField({ path, label, options, help }: { path: string; label: string; options: (string | [string, string])[]; help?: string }) {
  const { draft, set, sources } = useContext(EditorCtx);
  return (
    <Field label={label} source={sources[path]?.source} help={help}>
      <select className="input" value={String(getPath(draft, path) ?? "")} onChange={(e) => set(path, e.target.value)}>
        {options.map((o) => (Array.isArray(o) ? <option key={o[0]} value={o[0]}>{o[1]}</option> : <option key={o} value={o}>{o}</option>))}
      </select>
    </Field>
  );
}

export function BoolField({ path, label }: { path: string; label: string }) {
  const { draft, set, sources } = useContext(EditorCtx);
  return (
    <label className="flex items-center gap-2 text-[13px] mt-4">
      <input type="checkbox" checked={!!getPath(draft, path)} onChange={(e) => set(path, e.target.checked)} />
      {label}
      {sources[path]?.source === "default-assumption" && <span className="text-[10px] text-ink-3">(assumed)</span>}
    </label>
  );
}

export function Group({ title, children, note }: { title: string; children: ReactNode; note?: string }) {
  return (
    <fieldset className="card p-4">
      <legend className="px-1 font-semibold text-[14px]">{title}</legend>
      {note && <p className="text-[12px] text-ink-2 mb-2">{note}</p>}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-3">{children}</div>
    </fieldset>
  );
}
