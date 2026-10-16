import { useState } from "react";
import { planTrip } from "./api";
import PlaceField from "./PlaceField";
import { defaultDeparture, formatDuration, formatMiles, formatWhen, hourLabel } from "./format";
import MapView from "./MapView";
import { RISK_COLOR, RISK_LABEL, ROUTE_COLOR } from "./risk";

export default function App() {
  const [origin, setOrigin] = useState("Chicago, IL");
  const [destination, setDestination] = useState("Indianapolis, IN");
  const [departure, setDeparture] = useState(defaultDeparture);
  const [loadLb, setLoadLb] = useState("42000");
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [hour, setHour] = useState(0);

  const selected = result?.routes.find((route) => route.id === selectedId) || null;

  async function onSubmit(event) {
    event.preventDefault();
    setStatus("loading");
    setError("");
    try {
      const data = await planTrip({
        origin,
        destination,
        departure: new Date(departure).toISOString(),
        load_lb: Number(loadLb),
      });
      setResult(data);
      setSelectedId(data.recommended_route_id);
      setHour(0);
      setStatus("ready");
    } catch (err) {
      setStatus("idle");
      setError(err.message);
    }
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <header className="title">
          <h1>SafeRoute</h1>
          <p>Compare three truck routes by weather, load, and time.</p>
        </header>

        <form onSubmit={onSubmit}>
          <PlaceField id="origin" label="Origin" value={origin} onChange={setOrigin} />
          <PlaceField id="destination" label="Destination" value={destination} onChange={setDestination} />

          <label htmlFor="departure">Departure</label>
          <input id="departure" type="datetime-local" value={departure} onChange={(event) => setDeparture(event.target.value)} required />

          <label htmlFor="load">Load weight (lb)</label>
          <input id="load" type="number" min="0" max="200000" step="100" value={loadLb} onChange={(event) => setLoadLb(event.target.value)} required />

          <button type="submit" disabled={status === "loading"}>
            {status === "loading" ? "Comparing routes…" : "Compare routes"}
          </button>
          {status === "loading" && <p className="hint">Checking the roads and the forecast. This takes a few seconds.</p>}
        </form>

        {error && <p className="error">{error}</p>}

        {result && (
          <section className="results">
            <p className="matched">
              {result.origin.label} → {result.destination.label}
            </p>
            <p className="hint">Weather checked every {result.sample_every_miles} miles. A stretch uses the worse of its two ends.</p>
            {result.note && <p className="hint">{result.note}</p>}
            {result.caution && <p className="caution">{result.caution}</p>}

            {result.routes.map((route) => (
              <button
                key={route.id}
                type="button"
                className={route.id === selectedId ? "card selected" : "card"}
                onClick={() => setSelectedId(route.id)}
              >
                <div className="card-top">
                  <strong>
                    <i style={{ background: ROUTE_COLOR[route.id] }} />
                    {route.name}
                  </strong>
                  {route.recommended && <span className="badge">Recommended</span>}
                </div>
                <div className="meta">{formatMiles(route.distance_miles)} · {formatDuration(route.duration_seconds)}</div>
                <div className="stats">
                  <span className={route.no_travel_miles > 0 ? "bad" : ""}>No-travel {Math.round(route.no_travel_miles)} mi</span>
                  <span className={route.severe_miles > 0 ? "warn" : ""}>Severe {Math.round(route.severe_miles)} mi</span>
                  <span>High {Math.round(route.high_miles)} mi</span>
                </div>
                <div className="meta">Average risk {route.average_risk.toFixed(1)} · {route.checkpoints.length} checks</div>
              </button>
            ))}

            <div className="slider">
              <div className="card-top">
                <h2>Corridor forecast</h2>
                <span>{hourLabel(result.departure, hour)}</span>
              </div>
              <input
                type="range"
                min="0"
                max="48"
                step="1"
                value={hour}
                onChange={(event) => setHour(Number(event.target.value))}
                aria-label="Forecast hour"
              />
              <p className="hint">Hour 0 is the departure hour. The slider shows risk along the corridor for the next 48 hours. Route scores stay tied to each checkpoint’s arrival time.</p>
            </div>

            {selected && (
              <div className="checks">
                <h2>{selected.name} checkpoints</h2>
                <ul>
                  {selected.checkpoints.map((checkpoint) => (
                    <li key={`${checkpoint.mile}-${checkpoint.lat}`}>
                      <i style={{ background: RISK_COLOR[checkpoint.risk] }} />
                      <span>{Math.round(checkpoint.mile)} mi</span>
                      <span>{formatWhen(checkpoint.eta)}</span>
                      <span>{RISK_LABEL[checkpoint.risk]}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        )}

        <details className="rules">
          <summary>How a route is chosen</summary>
          <p>Wind, rain, and snow are scored at each check. The check keeps the worst score. Wind of 55 mph or more is no-travel. A load over 30,000 lb makes 45–54 mph no-travel. A load over 40,000 lb makes 35–44 mph severe.</p>
          <p>The recommended route has the fewest no-travel miles, then the fewest severe miles, then the fewest high miles, then the lowest average risk, then the shortest time.</p>
        </details>

        <p className="sources">Routes from OSRM. Weather from Open-Meteo. Map from OpenStreetMap.</p>
      </aside>

      <div className="map-wrap">
        <MapView result={result} selectedId={selectedId} onSelect={setSelectedId} hour={hour} />
        <div className="legend">
          {Object.entries(RISK_LABEL).map(([key, label]) => (
            <span key={key}><i style={{ background: RISK_COLOR[key] }} />{label}</span>
          ))}
        </div>
        {result && <div className="hour-chip">{hourLabel(result.departure, hour)}</div>}
        {!result && <div className="empty-chip">Enter a trip to compare routes.</div>}
      </div>
    </div>
  );
}
