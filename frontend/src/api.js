import { fetchForecast, fetchRoutes, findPlaces } from "./providers";

const SERVER_TIMEOUT_MS = 90_000;

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

async function postJson(path, body) {
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(SERVER_TIMEOUT_MS),
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
  const payloads = [await fetchRoutes([origin, destination], 3)];
  let step = await postJson("/api/prepare", {
    origin,
    destination,
    departure: body.departure,
    osrm: payloads,
  });
  if (step.status === "via" && step.calls?.length) {
    const extra = await Promise.all(
      step.calls.map((places) =>
        fetchRoutes(places).catch(() => ({ code: "NoRoute" })),
      ),
    );
    payloads.push(...extra);
    step = await postJson("/api/prepare", {
      origin,
      destination,
      departure: body.departure,
      osrm: payloads,
    });
  }
  if (step.status !== "ready") {
    throw new Error("No driving route was found between those places.");
  }
  const weather = await Promise.all(
    step.batches.map((points) => fetchForecast(points, step.forecast_days)),
  );
  return postJson("/api/finish", {
    origin,
    destination,
    departure: body.departure,
    load_lb: body.load_lb,
    routes: step.routes,
    weather,
  });
}
