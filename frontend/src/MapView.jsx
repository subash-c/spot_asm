import { useEffect, useRef } from "react";
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer, Tooltip, useMap } from "react-leaflet";
import L from "leaflet";
import { HEAT_INTENSITY, RISK_COLOR, RISK_LABEL, ROUTE_COLOR } from "./risk";
import { formatWhen } from "./format";

const HEAT_OPTIONS = {
  radius: 34,
  blur: 28,
  maxZoom: 7,
  max: 1,
  minOpacity: 0.35,
  gradient: {
    0.22: "#2f9e62",
    0.45: "#d9a406",
    0.68: "#e06a1f",
    0.86: "#d23b3b",
    1: "#6d2f78",
  },
};

function toHeat(points, hour) {
  const rows = [];
  for (const point of points || []) {
    const risk = point.risk?.[hour];
    if (!risk) continue;
    rows.push([point.lat, point.lon, HEAT_INTENSITY[risk]]);
  }
  return rows;
}

function HeatLayer({ points, hour }) {
  const map = useMap();
  const heatRef = useRef(null);
  const dataRef = useRef({ points, hour });
  dataRef.current = { points, hour };

  useEffect(() => {
    let cancelled = false;
    let layer = null;
    // leaflet.heat attaches itself to the global L. Vite runs static
    // imports before this assignment, so load the plugin after that.
    window.L = L;
    import("leaflet.heat").then(() => {
      if (cancelled) return;
      const current = dataRef.current;
      layer = L.heatLayer(toHeat(current.points, current.hour), HEAT_OPTIONS).addTo(map);
      const canvas = layer._canvas;
      const pane = map.getPanes().overlayPane;
      if (canvas) {
        canvas.style.pointerEvents = "none";
        if (pane.firstChild !== canvas) pane.insertBefore(canvas, pane.firstChild);
      }
      heatRef.current = layer;
    });
    return () => {
      cancelled = true;
      if (layer) map.removeLayer(layer);
      if (heatRef.current === layer) heatRef.current = null;
    };
  }, [map]);

  useEffect(() => {
    if (heatRef.current) {
      heatRef.current.setLatLngs(toHeat(points, hour));
    }
  }, [points, hour]);

  return null;
}

function FitBounds({ positions, token }) {
  const map = useMap();
  useEffect(() => {
    if (positions.length > 1) {
      map.fitBounds(positions, { padding: [48, 48] });
    }
  }, [map, token]);
  return null;
}

function routePath(route) {
  const path = [];
  for (const segment of route.segments) {
    for (const point of segment.path) {
      const last = path[path.length - 1];
      if (!last || last[0] !== point[0] || last[1] !== point[1]) path.push(point);
    }
  }
  return path;
}

export default function MapView({ result, selectedId, onSelect, hour }) {
  const routes = result?.routes || [];
  const selected = routes.find((route) => route.id === selectedId);
  const others = routes.filter((route) => route.id !== selectedId);
  const positions = routes.flatMap((route) => routePath(route));

  return (
    <MapContainer center={[39.5, -96]} zoom={4} preferCanvas style={{ height: "100%", width: "100%" }}>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      {result && <HeatLayer points={result.heatmap.points} hour={hour} />}
      {result && <FitBounds positions={positions} token={result.departure + result.origin.label + result.destination.label} />}
      {others.map((route) => (
        <Polyline
          key={route.id}
          positions={routePath(route)}
          pathOptions={{ color: ROUTE_COLOR[route.id], weight: 4, opacity: 0.8 }}
          eventHandlers={{ click: () => onSelect(route.id) }}
        >
          <Tooltip>{route.name}</Tooltip>
        </Polyline>
      ))}
      {selected?.segments.map((segment, index) => (
        <Polyline
          key={`${selected.id}-case-${index}`}
          positions={segment.path}
          pathOptions={{ color: "#ffffff", weight: 11, opacity: 0.95 }}
        />
      ))}
      {selected?.segments.map((segment, index) => (
        <Polyline
          key={`${selected.id}-risk-${index}`}
          positions={segment.path}
          pathOptions={{ color: RISK_COLOR[segment.risk], weight: 5 }}
          eventHandlers={{ click: () => onSelect(selected.id) }}
        />
      ))}
      {selected?.checkpoints.map((checkpoint) => (
        <CircleMarker
          key={`${checkpoint.mile}-${checkpoint.lat}`}
          center={[checkpoint.lat, checkpoint.lon]}
          radius={7}
          pathOptions={{
            color: "#ffffff",
            weight: 2,
            fillColor: RISK_COLOR[checkpoint.risk],
            fillOpacity: 1,
          }}
        >
          <Popup>
            <strong>{RISK_LABEL[checkpoint.risk]}</strong>
            <div>Mile {Math.round(checkpoint.mile)}</div>
            <div>ETA {formatWhen(checkpoint.eta)}</div>
            <div>Wind {Math.round(checkpoint.wind_mph)} mph · {RISK_LABEL[checkpoint.wind_risk]}</div>
            <div>Rain {checkpoint.rain_in_hr.toFixed(2)} in/hr · {RISK_LABEL[checkpoint.rain_risk]}</div>
            <div>Snow {checkpoint.snow_in_hr.toFixed(2)} in/hr · {RISK_LABEL[checkpoint.snow_risk]}</div>
            {checkpoint.note && <div>{checkpoint.note}</div>}
            {checkpoint.forecast_limited && <div>Closest available forecast hour.</div>}
          </Popup>
        </CircleMarker>
      ))}
    </MapContainer>
  );
}
