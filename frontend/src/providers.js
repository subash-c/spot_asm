const ROUTERS = [
  "https://router.project-osrm.org",
  "https://routing.openstreetmap.de/routed-car",
];

const REQUEST_TIMEOUT_MS = 20_000;

async function getJson(url) {
  let response;
  try {
    response = await fetch(url, {
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

  let lastError = null;
  try {
    const payload = await getJson(
      `https://photon.komoot.io/api/?q=${encodeURIComponent(query)}&limit=${limit}&lang=en`,
    );
    const places = photonPlaces(payload);
    if (places.length) return places;
  } catch (error) {
    lastError = error;
  }

  try {
    const payload = await getJson(
      `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(query)}&count=${limit}&language=en&format=json`,
    );
    const places = meteoPlaces(payload);
    if (places.length) return places;
  } catch (error) {
    lastError = error;
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
  throw lastError || new Error("A map service is busy. Wait a moment and try again.");
}

