export async function searchPlaces(query) {
  try {
    const response = await fetch(`/api/places?q=${encodeURIComponent(query)}`);
    if (!response.ok) return [];
    const data = await response.json();
    return data.places || [];
  } catch {
    return [];
  }
}

export async function planTrip(body) {
  let response;
  try {
    response = await fetch("/api/plan", {
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
