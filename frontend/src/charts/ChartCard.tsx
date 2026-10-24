import type { EChartsOption } from "echarts";
import { useState, type ReactNode } from "react";
import { downloadCsv } from "../lib/format";
import { EChart } from "./EChart";

export interface TableData {
  columns: string[];
  rows: (string | number | boolean | null | undefined)[][];
}

interface Props {
  id: string;
  number?: number;
  title: string;
  units: string;
  period?: string;
  source: string;
  assumptions?: string;
  option?: EChartsOption;
  table?: TableData;
  height?: number;
  empty?: ReactNode;
  children?: ReactNode;
  className?: string;
  onChartClick?: (params: any) => void;
}

/**
 * Every chart: title, units, period, source/model reference, key assumptions, a table-view twin
 * (the WCAG-clean equivalent, also the relief for low-contrast series colours) and CSV download.
 * When there is no data the card shows an explicit empty state — never placeholder numbers.
 */
export function ChartCard({ id, number, title, units, period, source, assumptions, option, table, height = 280, empty, children, className, onChartClick }: Props) {
  const [asTable, setAsTable] = useState(false);
  const hasData = !empty && (option || children);
  return (
    <section className={`card p-4 flex flex-col gap-2 min-w-0 ${className ?? ""}`} aria-labelledby={`${id}-title`} data-chart-id={id}>
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 id={`${id}-title`} className="font-semibold text-[14px] leading-snug">
            {number !== undefined && <span className="text-ink-3 mr-1">{number}.</span>}
            {title}
          </h3>
          <p className="text-[12px] text-ink-2">
            {units}
            {period ? ` · ${period}` : ""}
          </p>
        </div>
        {hasData && table && (
          <div className="flex gap-1 shrink-0">
            <button className="btn !py-1 !px-2 text-[12px]" aria-pressed={asTable} onClick={() => setAsTable((v) => !v)}>
              {asTable ? "Chart" : "Table"}
            </button>
            <button className="btn !py-1 !px-2 text-[12px]" onClick={() => downloadCsv(`${id}.csv`, table.columns, table.rows)} aria-label={`Download ${title} data as CSV`}>
              CSV
            </button>
          </div>
        )}
      </header>
      {!hasData ? (
        <div className="flex-1 flex items-center justify-center text-center text-[13px] text-ink-2 rounded-md border border-dashed border-axis p-6" style={{ minHeight: Math.min(height, 200) }}>
          <div>{empty ?? "No data available."}</div>
        </div>
      ) : asTable && table ? (
        <div className="overflow-auto" style={{ maxHeight: height + 40 }}>
          <table className="w-full text-[12px] tnum">
            <thead className="sticky top-0 bg-surface">
              <tr>{table.columns.map((c) => <th key={c} className="text-left font-semibold px-2 py-1 border-b border-line">{c}</th>)}</tr>
            </thead>
            <tbody>
              {table.rows.slice(0, 2000).map((r, i) => (
                <tr key={i} className="border-b border-line">
                  {r.map((v, j) => <td key={j} className="px-2 py-0.5">{typeof v === "number" ? (Number.isInteger(v) ? v : v.toFixed(3)) : String(v ?? "")}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
          {table.rows.length > 2000 && <p className="text-[11px] text-ink-3 p-1">First 2,000 of {table.rows.length} rows shown; CSV contains all rows.</p>}
        </div>
      ) : option ? (
        <EChart option={option} height={height} onClick={onChartClick} ariaLabel={`${title} (${units}). Use the Table button for the underlying values.`} />
      ) : (
        children
      )}
      <footer className="text-[11px] text-ink-3 leading-snug">
        <span className="font-medium text-ink-2">Source:</span> {source}
        {assumptions ? <><br /><span className="font-medium text-ink-2">Assumptions:</span> {assumptions}</> : null}
      </footer>
    </section>
  );
}
