import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect } from "react";
import { useTokens } from "../charts/useTokens";
import { CircleMarker, MapContainer, TileLayer, Tooltip, useMap, useMapEvents } from "react-leaflet";

interface Marker {
  lat: number;
  lon: number;
  label: string;
  kind?: "site" | "data" | "other";
}

function ClickHandler({ onPick }: { onPick?: (lat: number, lon: number) => void }) {
  useMapEvents({ click: (e) => onPick?.(+e.latlng.lat.toFixed(5), +e.latlng.lng.toFixed(5)) });
  return null;
}

function Fit({ markers }: { markers: Marker[] }) {
  const map = useMap();
  useEffect(() => {
    if (!markers.length) return;
    if (markers.length === 1) map.setView([markers[0].lat, markers[0].lon], Math.max(map.getZoom(), 8));
    else map.fitBounds(L.latLngBounds(markers.map((m) => [m.lat, m.lon] as [number, number])).pad(0.3));
  }, [map, markers]);
  return null;
}

/** Site map: click to pick coordinates; markers show the site and climate-data source locations. */
export function SiteMap({ markers, onPick, height = 320 }: { markers: Marker[]; onPick?: (lat: number, lon: number) => void; height?: number }) {
  const t = useTokens();
  const colors = { site: t.series[0], data: t.series[1], other: t.series[2] };
  const center: [number, number] = markers.length ? [markers[0].lat, markers[0].lon] : [20, 0];
  return (
    <div className="rounded-lg overflow-hidden border border-line" style={{ height }}>
      <MapContainer center={center} zoom={markers.length ? 8 : 2} style={{ height: "100%", width: "100%" }} scrollWheelZoom>
        <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
        <ClickHandler onPick={onPick} />
        <Fit markers={markers} />
        {markers.map((m, i) => (
          <CircleMarker key={i} center={[m.lat, m.lon]} radius={m.kind === "site" ? 8 : 6}
            pathOptions={{ color: t.surface, weight: 2, fillColor: colors[m.kind ?? "site"], fillOpacity: 1 }}>
            <Tooltip>{m.label}</Tooltip>
          </CircleMarker>
        ))}
      </MapContainer>
    </div>
  );
}
