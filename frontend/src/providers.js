const ROUTERS = [
  "https://router.project-osrm.org",
  "https://routing.openstreetmap.de/routed-car",
];

const REQUEST_TIMEOUT_MS = 20_000;

async function getJson(url, options = {}) {
  let response;
  try {
    response = await fetch(url, {
      ...options,
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
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
  return [...new Set(parts.filter(Boolean))].join(", ");
}

function photonPlaces(payload) {
  const places = [];
  const seen = new Set();
  for (const feature of payload.features || []) {
    const props = feature.properties || {};
    const coordinates = feature.geometry?.coordinates || [];
    const label = placeLabel([
      props.name || props.city || props.street,
      props.state,
      props.country,
    ]);
    if (coordinates.length < 2 || !label || seen.has(label)) continue;
    seen.add(label);
    places.push({
      label,
      lat: Number(coordinates[1]),
      lon: Number(coordinates[0]),
    });
  }
  return places;
}

function meteoPlaces(payload) {
  const places = [];
  const seen = new Set();
  for (const item of payload.results || []) {
    const label = placeLabel([item.name, item.admin1, item.country]);
    if (
      !label ||
      seen.has(label) ||
      item.latitude == null ||
      item.longitude == null
    ) {
      continue;
    }
    seen.add(label);
    places.push({
      label,
      lat: Number(item.latitude),
      lon: Number(item.longitude),
    });
  }
  return places;
}

export async function findPlaces(text, limit = 5) {
  const query = text.trim();
  if (query.length < 2) return [];

  const sources = [
    async () => meteoPlaces(await getJson(
      `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(query)}&count=${limit}&language=en&format=json`,
    )),
    async () => photonPlaces(await getJson(
      `https://photon.komoot.io/api/?q=${encodeURIComponent(query)}&limit=${limit}&lang=en`,
    )),
  ];
  let lastError = null;
  for (const source of sources) {
    try {
      const places = await source();
      if (places.length) return places;
    } catch (error) {
      lastError = error;
    }
  }
  if (lastError) throw lastError;
  return [];
}

export async function fetchRoutes(places, alternatives = 0) {
  const coordinates = places
    .map((place) => `${Number(place.lon).toFixed(5)},${Number(place.lat).toFixed(5)}`)
    .join(";");
  const params = new URLSearchParams({
    overview: "full",
    geometries: "geojson",
    steps: "false",
    alternatives: alternatives || "false",
  });

  let lastError = null;
  for (const router of ROUTERS) {
    try {
      return await getJson(
        `${router}/route/v1/driving/${coordinates}?${params}`,
      );
    } catch (error) {
      lastError = error;
    }
  }
  try {
    return await valhallaRoutes(places, alternatives);
  } catch (error) {
    lastError = error;
  }
  throw lastError || new Error("A map service is busy. Wait a moment and try again.");
}

async function valhallaRoutes(places, alternatives) {
  const payload = await getJson("https://valhalla1.openstreetmap.de/route", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      locations: places.map((place) => ({
        lat: Number(place.lat),
        lon: Number(place.lon),
      })),
      costing: "auto",
      alternates: alternatives ? Math.max(0, Number(alternatives) - 1) : 0,
      directions_options: { units: "kilometers" },
    }),
  });
  const trips = [
    payload.trip,
    ...(payload.alternates || []).map((item) => item.trip),
  ].filter(Boolean);
  const routes = trips.map(valhallaTrip).filter(Boolean);
  return routes.length ? { code: "Ok", routes } : { code: "NoRoute" };
}

function valhallaTrip(trip) {
  const coordinates = [];
  let distance = 0;
  let duration = 0;
  for (const leg of trip.legs || []) {
    const line = decodePolyline(leg.shape || "");
    if (coordinates.length && line.length) line.shift();
    coordinates.push(...line);
    distance += Number(leg.summary?.length || 0) * 1000;
    duration += Number(leg.summary?.time || 0);
  }
  if (coordinates.length < 2) return null;
  return { distance, duration, geometry: { coordinates } };
}

function decodePolyline(value) {
  const coordinates = [];
  let index = 0;
  let latitude = 0;
  let longitude = 0;
  while (index < value.length) {
    const latitudeChange = decodeChunk();
    const longitudeChange = decodeChunk();
    latitude += latitudeChange;
    longitude += longitudeChange;
    coordinates.push([longitude / 1e6, latitude / 1e6]);
  }
  return coordinates;

  function decodeChunk() {
    let result = 0;
    let shift = 0;
    let byte = 0;
    do {
      if (index >= value.length) return 0;
      byte = value.charCodeAt(index++) - 63;
      result |= (byte & 0x1f) << shift;
      shift += 5;
    } while (byte >= 0x20);
    return result & 1 ? ~(result >> 1) : result >> 1;
  }
}

export function fetchForecast(points, forecastDays) {
  const params = new URLSearchParams({
    latitude: points.map((point) => Number(point.lat).toFixed(4)).join(","),
    longitude: points.map((point) => Number(point.lon).toFixed(4)).join(","),
    hourly: "rain,snowfall,wind_speed_10m",
    wind_speed_unit: "mph",
    precipitation_unit: "inch",
    timezone: "UTC",
    forecast_days: String(forecastDays),
  });
  return getJson(`https://api.open-meteo.com/v1/forecast?${params}`);
}

