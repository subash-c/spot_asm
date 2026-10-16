const ROUTERS = [
  "https://router.project-osrm.org",
  "https://routing.openstreetmap.de/routed-car",
];

async function getJson(url) {
  let response;
  try {
    response = await fetch(url, { signal: AbortSignal.timeout(20000) });
  } catch {
    throw new Error("A map or weather service did not respond. Try again.");
  }
  if (response.status === 429) {
    throw new Error("A map service is busy. Wait a moment and try again.");
  }
  if (!response.ok) {
    throw new Error("A map or weather service returned an error. Try again.");
  }
  try {
    return await response.json();
  } catch {
    throw new Error("A map or weather service returned an unreadable response.");
  }
}

function placeLabel(parts) {
  const unique = [];
  for (const part of parts) {
    if (part && !unique.includes(part)) unique.push(part);
  }
  return unique.join(", ");
}

function photonPlaces(payload) {
  const places = [];
  const seen = new Set();
  for (const feature of payload.features || []) {
    const props = feature.properties || {};
    const coordinates = (feature.geometry || {}).coordinates || [];
    if (coordinates.length < 2) continue;
    const label = placeLabel([props.name || props.city || props.street, props.state, props.country]);
    if (!label || seen.has(label)) continue;
    seen.add(label);
    places.push({ label, lat: Number(coordinates[1]), lon: Number(coordinates[0]) });
  }
  return places;
}

function meteoPlaces(payload) {
  const places = [];
  const seen = new Set();
  for (const item of payload.results || []) {
    const label = placeLabel([item.name, item.admin1, item.country]);
    if (!label || seen.has(label) || item.latitude == null || item.longitude == null) continue;
    seen.add(label);
    places.push({ label, lat: Number(item.latitude), lon: Number(item.longitude) });
  }
  return places;
}

async function findPlaces(text, limit) {
  const query = text.trim();
  if (query.length < 2) return [];
  let lastError = null;
  try {
    const photon = await getJson(
      `https://photon.komoot.io/api/?q=${encodeURIComponent(query)}&limit=${limit}&lang=en`,
    );
    const places = photonPlaces(photon);
    if (places.length) return places;
  } catch (err) {
    lastError = err;
  }
  try {
    const meteo = await getJson(
      `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(query)}&count=${limit}&language=en&format=json`,
    );
    const places = meteoPlaces(meteo);
    if (places.length) return places;
  } catch (err) {
    lastError = err;
  }
  if (lastError) throw lastError;
  return [];
}

export async function searchPlaces(query) {
  try {
    return await findPlaces(query, 5);
  } catch {
    return [];
  }
}

async function locate(text) {
  const places = await findPlaces(text, 1);
  if (!places.length) {
    throw new Error(`Could not find "${text}". Try a city and state.`);
  }
  return places[0];
}

async function drivingPayload(places, alternatives) {
  const coord = places
    .map((place) => `${Number(place.lon).toFixed(5)},${Number(place.lat).toFixed(5)}`)
    .join(";");
  const query = `overview=full&geometries=geojson&steps=false&alternatives=${alternatives ? alternatives : "false"}`;
  let lastError = null;
  for (const base of ROUTERS) {
    try {
      return await getJson(`${base}/route/v1/driving/${coord}?${query}`);
    } catch (err) {
      lastError = err;
    }
  }
  throw lastError || new Error("A map service is busy. Wait a moment and try again.");
}

async function forecast(points, days) {
  const params = new URLSearchParams({
    latitude: points.map((point) => Number(point.lat).toFixed(4)).join(","),
    longitude: points.map((point) => Number(point.lon).toFixed(4)).join(","),
    hourly: "rain,snowfall,wind_speed_10m",
    wind_speed_unit: "mph",
    precipitation_unit: "inch",
    timezone: "UTC",
    forecast_days: String(days),
  });
  return getJson(`https://api.open-meteo.com/v1/forecast?${params}`);
}

async function postJson(path, body) {
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error("Could not reach the server. Start Django on port 8000.");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || "Could not plan that trip.");
  }
  return data;
}

export async function planTrip(body) {
  const [origin, destination] = await Promise.all([
    locate(body.origin),
    locate(body.destination),
  ]);
  const payloads = [await drivingPayload([origin, destination], 3)];
  let step = await postJson("/api/prepare", {
    origin,
    destination,
    departure: body.departure,
    osrm: payloads,
  });
  if (step.status === "via" && step.calls?.length) {
    const extra = await Promise.all(
      step.calls.map((places) => drivingPayload(places, 0).catch(() => ({ code: "NoRoute" }))),
    );
    payloads.push(...extra);
    step = await postJson("/api/prepare", {
      origin,
      destination,
      departure: body.departure,
      osrm: payloads,
    });
  }
  if (step.status !== "weather") {
    throw new Error("No driving route was found between those places.");
  }
  const weather = await Promise.all(step.batches.map((points) => forecast(points, step.forecast_days)));
  return postJson("/api/finish", {
    origin,
    destination,
    departure: body.departure,
    load_lb: body.load_lb,
    routes: step.routes,
    weather,
  });
}
