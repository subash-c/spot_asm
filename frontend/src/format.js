export function formatMiles(miles) {
  return `${Math.round(miles).toLocaleString()} mi`;
}

export function formatDuration(seconds) {
  const minutes = Math.round(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours <= 0) return `${rest} min`;
  return `${hours} h ${rest} min`;
}

export function formatWhen(iso) {
  return new Date(iso).toLocaleString([], {
    weekday: "short",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function hourLabel(departure, hour) {
  const time = new Date(new Date(departure).getTime() + hour * 3600000);
  const when = time.toLocaleString([], {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
  if (hour === 0) return `Departure · ${when}`;
  return `+${hour} h · ${when}`;
}

export function defaultDeparture() {
  const date = new Date();
  date.setMinutes(0, 0, 0);
  date.setHours(date.getHours() + 1);
  return localDateTime(date);
}

export function departureBounds() {
  const now = new Date();
  const min = new Date(now.getTime() - 3 * 60 * 60 * 1000);
  const max = new Date(now.getTime() + 6 * 24 * 60 * 60 * 1000);
  return { min: localDateTime(min), max: localDateTime(max) };
}

function localDateTime(date) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:00`;
}
