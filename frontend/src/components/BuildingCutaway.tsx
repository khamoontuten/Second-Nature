import { useTokens } from "../charts/useTokens";

/**
 * Schematic building section that highlights the strategies in a design (cool air in blue, warm air in red,
 * nature-inspired elements in green). It is a diagram of *which* strategies are present, not a result.
 */
export function BuildingCutaway({ active }: { active: string[] }) {
  const t = useTokens();
  const on = (k: string) => active.some((a) => a.startsWith(k));
  // SVG presentation attributes cannot resolve CSS variables, so concrete token values are used.
  const green = t.series[5];
  const cool = t.series[0];
  const warm = t.series[7];
  const ink = t.text2;
  const dim = t.axis;
  const items = [
    { k: "stack", label: "Termite stack" },
    { k: "eahe", label: "Burrow earth-air tunnels" },
    { k: "facade", label: "Fish-scale facade" },
    { k: "envelope", label: "Shell envelope" },
    { k: "economiser", label: "Economiser" },
    { k: "iec", label: "Indirect evaporative" },
    { k: "storage", label: "Thermal storage" },
    { k: "radiative", label: "Radiative roof" },
  ];
  return (
    <figure className="card p-3">
      <svg viewBox="0 0 520 250" className="w-full h-auto" role="img"
        aria-label={`Building section highlighting: ${items.filter((i) => on(i.k)).map((i) => i.label).join(", ") || "no added strategies"}`}>
        <defs>
          <marker id="arr-c" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0L10,5L0,10z" fill={cool} /></marker>
          <marker id="arr-w" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0L10,5L0,10z" fill={warm} /></marker>
        </defs>
        <line x1="10" y1="190" x2="510" y2="190" stroke={ink} strokeWidth="1" />
        {/* hall */}
        <rect x="140" y="110" width="240" height="80" fill="none" stroke={on("envelope") ? green : ink} strokeWidth={on("envelope") ? 4 : 1.5} />
        {[0, 1, 2, 3, 4].map((i) => <rect key={i} x={165 + i * 42} y={140} width={14} height={50} fill={dim} />)}
        <text x="260" y="128" textAnchor="middle" fontSize="11" fill={ink}>IT hall (contained aisles)</text>
        {/* roof */}
        <line x1="140" y1="108" x2="380" y2="108" stroke={on("radiative") ? green : dim} strokeWidth={on("radiative") ? 4 : 2} />
        {/* chimney */}
        <rect x="330" y="20" width="30" height="90" fill="none" stroke={on("stack") ? green : dim} strokeWidth={on("stack") ? 3 : 1} strokeDasharray={on("stack") ? "" : "3 3"} />
        {on("stack") && <path d="M345 105 L345 30" stroke={warm} strokeWidth="2" markerEnd="url(#arr-w)" />}
        {/* facade scales */}
        {[0, 1, 2, 3].map((i) => (
          <path key={i} d={`M136 ${116 + i * 18} l-12 10`} stroke={on("facade") ? green : dim} strokeWidth={on("facade") ? 3 : 1} />
        ))}
        {/* economiser / inlet air */}
        {(on("economiser") || on("stack")) && <path d="M60 170 L135 170" stroke={cool} strokeWidth="2" markerEnd="url(#arr-c)" />}
        {/* earth tube */}
        <path d="M40 160 L40 225 L200 225 L200 192" fill="none" stroke={on("eahe") ? green : dim} strokeWidth={on("eahe") ? 5 : 1.5}
          strokeDasharray={on("eahe") ? "" : "3 3"} />
        {on("eahe") && <path d="M60 225 L180 225" stroke={cool} strokeWidth="2" markerEnd="url(#arr-c)" />}
        <text x="120" y="244" textAnchor="middle" fontSize="10" fill={ink}>soil</text>
        {/* IEC / plant */}
        <rect x="400" y="140" width="60" height="50" fill="none" stroke={on("iec") ? green : ink} strokeWidth={on("iec") ? 3 : 1} />
        <text x="430" y="168" textAnchor="middle" fontSize="10" fill={ink}>plant</text>
        <rect x="470" y="150" width="30" height="40" fill="none" stroke={on("storage") ? green : dim} strokeWidth={on("storage") ? 3 : 1} />
        <text x="485" y="146" textAnchor="middle" fontSize="9" fill={ink}>TES</text>
      </svg>
      <figcaption className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-ink-2 mt-1">
        {items.map((i) => (
          <span key={i.k} className="flex items-center gap-1">
            <span aria-hidden className="inline-block w-3 h-[3px]" style={{ background: on(i.k) ? green : dim }} />
            {i.label} {on(i.k) ? "✓" : ""}
          </span>
        ))}
        <span className="flex items-center gap-1"><span aria-hidden className="inline-block w-3 h-[2px]" style={{ background: cool }} />cool air</span>
        <span className="flex items-center gap-1"><span aria-hidden className="inline-block w-3 h-[2px]" style={{ background: warm }} />exhaust air</span>
      </figcaption>
    </figure>
  );
}
