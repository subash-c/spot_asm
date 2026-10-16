"""Geocode a trip, fetch three driving routes, and score them with weather."""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

from .geo import (
    bearing_deg,
    corridor_points,
    destination_point,
    group_segments,
    haversine_miles,
    mean_separation,
    point_at_distance,
    polyline_length,
    sample_polyline,
    scale_miles,
    shape_points,
    weather_interval_miles,
)
from .risk import assess, rank_routes, summarize_checkpoints, worst

logger = logging.getLogger(__name__)

USER_AGENT = "SafeRoute/1.0 (truck route planner demo)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}
METERS_PER_MILE = 1609.344
NAMES = ("Route A", "Route B", "Route C")
# Public OSRM servers. The first is used until it refuses a request.
ROUTERS = (
    "https://routing.openstreetmap.de/routed-car",
    "https://router.project-osrm.org",
)
_preferred_router = ROUTERS[0]
_router_lock = threading.Lock()
_cache = {}
_cache_lock = threading.Lock()
_nominatim_lock = threading.Lock()
_nominatim_next = 0.0


class PlannerError(Exception):
    pass


def plan_trip(origin_text, destination_text, departure, load_lb):
    plan_key = (
        "plan",
        origin_text.strip().lower(),
        destination_text.strip().lower(),
        departure.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
        round(float(load_lb)),
    )
    cached = cache_get(plan_key)
    if cached:
        return cached

    with ThreadPoolExecutor(max_workers=2) as pool:
        origin_job = pool.submit(geocode, origin_text)
        destination_job = pool.submit(geocode, destination_text)
        origin = origin_job.result()
        destination = destination_job.result()

    if haversine_miles(origin["lat"], origin["lon"], destination["lat"], destination["lon"]) < 1:
        raise PlannerError("Origin and destination are the same place.")

    routes = fetch_routes(origin, destination)
    interval = weather_interval_miles(routes[0]["distance_miles"])
    horizon = departure + timedelta(hours=48)
    for route in routes:
        horizon = max(horizon, departure + timedelta(seconds=route["duration_seconds"]))

    weather_points = []
    prepared = []
    for route in routes:
        weather_samples = scale_miles(
            sample_polyline(route["coordinates"], interval),
            route["distance_miles"],
        )
        display_step = max(1.0, route["distance_miles"] / 350.0)
        display_samples = scale_miles(
            sample_polyline(route["coordinates"], display_step),
            route["distance_miles"],
        )
        for lat, lon, _mile in weather_samples:
            weather_points.append({"lat": lat, "lon": lon})
        prepared.append((route, weather_samples, display_samples))

    grid = corridor_points([route["coordinates"] for route in routes])
    weather_points.extend(grid)
    forecast_days = forecast_window(departure, horizon)
    weather = fetch_weather(weather_points, forecast_days)

    scored = []
    for index, (route, weather_samples, display_samples) in enumerate(prepared):
        scored.append(score_route(
            route, index, weather_samples, display_samples, weather, departure, load_lb, interval,
        ))

    ranked = rank_routes(scored)
    heatmap = []
    for point in grid:
        row = weather.get(weather_key(point["lat"], point["lon"]))
        if not row:
            continue
        heatmap.append({
            "lat": point["lat"],
            "lon": point["lon"],
            "risk": risk_hours(row, departure, load_lb),
        })

    caution = ""
    if ranked and ranked[0]["no_travel_miles"] > 0:
        caution = "The recommended route still has no-travel weather. It is the safest of these options."

    note = ""
    if len(ranked) < 3:
        note = f"Only {len(ranked)} distinct driving route{'s' if len(ranked) != 1 else ''} were found."

    result = {
        "origin": origin,
        "destination": destination,
        "departure": iso_z(departure),
        "load_lb": int(load_lb) if float(load_lb).is_integer() else load_lb,
        "sample_every_miles": interval,
        "recommended_route_id": ranked[0]["id"],
        "caution": caution,
        "note": note,
        "routes": ranked,
        "heatmap": {"points": heatmap},
    }
    cache_set(plan_key, result, 10 * 60)
    return result


def score_route(route, index, weather_samples, display_samples, weather, departure, load_lb, interval):
    checkpoints = []
    forecast_limited = False
    for lat, lon, mile in weather_samples:
        fraction = 0 if route["distance_miles"] == 0 else mile / route["distance_miles"]
        eta = departure + timedelta(seconds=route["duration_seconds"] * fraction)
        reading, limited = read_conditions(weather, lat, lon, eta)
        forecast_limited = forecast_limited or limited
        checked = assess(reading["wind"], reading["rain"], reading["snow"], load_lb)
        checkpoints.append({
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "mile": round(mile, 1),
            "eta": iso_z(eta),
            "forecast_limited": limited,
            **checked,
        })

    summary = summarize_checkpoints(checkpoints)

    def risk_at_mile(mile):
        for prev, current in zip(checkpoints, checkpoints[1:]):
            if mile <= current["mile"] + 1e-6:
                return worst(prev["risk"], current["risk"])
        return checkpoints[-1]["risk"]

    return {
        "id": f"route-{NAMES[index][-1].lower()}",
        "name": NAMES[index],
        "distance_miles": round(route["distance_miles"], 1),
        "duration_seconds": round(route["duration_seconds"]),
        "sample_every_miles": interval,
        "forecast_limited": forecast_limited,
        "segments": group_segments(display_samples, risk_at_mile),
        "checkpoints": checkpoints,
        **summary,
    }


def fetch_routes(origin, destination):
    start = (origin["lat"], origin["lon"])
    end = (destination["lat"], destination["lon"])
    direct = request_osrm([start, end], alternatives=3)
    if not direct:
        raise PlannerError("No driving route was found between those places.")
    direct.sort(key=lambda route: route["duration_seconds"])

    candidates = list(direct)
    if len(pick_three(direct)) < 3:
        # One detour at a time, and stop once three distinct roads exist.
        # A burst of routing calls is what gets the shared demo server to refuse us.
        for via in via_points(direct[0]):
            if len(pick_three(candidates)) >= 3:
                break
            try:
                candidates.extend(request_osrm([start, via, end], 0))
            except PlannerError:
                continue

    chosen = pick_three(candidates)
    if not chosen:
        raise PlannerError("No driving route was found between those places.")
    return chosen[:3]


def via_points(route):
    coordinates = route["coordinates"]
    total = polyline_length(coordinates) or route["distance_miles"]
    offset = min(120.0, max(25.0, route["distance_miles"] * 0.18))
    points = []
    for fraction, sign in ((0.5, 1), (0.5, -1), (0.35, 1), (0.65, -1)):
        center_mile = total * fraction
        center = point_at_distance(coordinates, center_mile)
        ahead = point_at_distance(coordinates, min(total, center_mile + max(5.0, total * 0.05)))
        behind = point_at_distance(coordinates, max(0.0, center_mile - max(5.0, total * 0.05)))
        heading = bearing_deg(behind[0], behind[1], ahead[0], ahead[1])
        points.append(destination_point(center[0], center[1], heading + (90 * sign), offset))
    return points


def pick_three(candidates):
    unique = []
    for route in candidates:
        if route["distance_miles"] <= 0 or len(route["coordinates"]) < 2:
            continue
        if unique and route["distance_miles"] > unique[0][0]["distance_miles"] * 2.4:
            continue
        shape = shape_points(route["coordinates"])
        similar = False
        for _kept, kept_shape in unique:
            if mean_separation(shape, kept_shape) < 15:
                similar = True
                break
        if not similar:
            unique.append((route, shape))

    if len(unique) <= 3:
        return [route for route, _shape in unique]

    chosen = [unique[0]]
    remaining = unique[1:]
    while remaining and len(chosen) < 3:
        best = None
        best_gap = -1
        for item in remaining:
            gap = min(mean_separation(item[1], kept[1]) for kept in chosen)
            if gap > best_gap:
                best = item
                best_gap = gap
        chosen.append(best)
        remaining.remove(best)
    return [route for route, _shape in chosen]


def request_osrm(places, alternatives):
    key = ("osrm", tuple((round(lat, 4), round(lon, 4)) for lat, lon in places), int(alternatives or 0))
    cached = cache_get(key)
    if cached is not None:
        return [dict(route) for route in cached]

    coord_text = ";".join(f"{lon:.5f},{lat:.5f}" for lat, lon in places)
    params = {
        "alternatives": alternatives if alternatives else "false",
        "overview": "full",
        "geometries": "geojson",
        "steps": "false",
    }
    payload = None
    last_error = None
    for base in router_order():
        try:
            payload = get_json(f"{base}/route/v1/driving/{coord_text}", params=params, timeout=20, retries=0)
            remember_router(base)
            last_error = None
            break
        except PlannerError as exc:
            logger.warning("router %s failed: %s", base, exc)
            last_error = exc
    if payload is None:
        time.sleep(0.7)
        base = router_order()[0]
        try:
            payload = get_json(f"{base}/route/v1/driving/{coord_text}", params=params, timeout=20, retries=0)
        except PlannerError:
            raise last_error or PlannerError("A map service is busy. Wait a moment and try again.")

    if payload.get("code") != "Ok":
        return []

    routes = []
    for route in payload.get("routes", []):
        coordinates = route.get("geometry", {}).get("coordinates") or []
        if len(coordinates) < 2:
            continue
        routes.append({
            "coordinates": coordinates,
            "distance_miles": route["distance"] / METERS_PER_MILE,
            "duration_seconds": route["duration"],
        })
    if routes:
        cache_set(key, routes, 6 * 60 * 60)
    return [dict(route) for route in routes]


def router_order():
    with _router_lock:
        preferred = _preferred_router
    return (preferred,) + tuple(base for base in ROUTERS if base != preferred)


def remember_router(base):
    global _preferred_router
    with _router_lock:
        _preferred_router = base


def geocode(text):
    matches = search_places(text, limit=1)
    if not matches:
        raise PlannerError(f'Could not find "{text}". Try a city and state.')
    return matches[0]


def search_places(text, limit=5):
    text = (text or "").strip()
    if len(text) < 2:
        return []
    key = ("places", text.lower(), limit)
    cached = cache_get(key)
    if cached is not None:
        return cached
    try:
        places = search_photon(text, limit)
    except PlannerError as exc:
        logger.warning("photon failed, trying nominatim: %s", exc)
        places = []
    if not places:
        places = search_nominatim(text, limit)
    cache_set(key, places, 24 * 60 * 60 if places else 10 * 60)
    return places


def search_photon(text, limit):
    payload = get_json(
        "https://photon.komoot.io/api/",
        params={"q": text, "limit": limit, "lang": "en"},
        timeout=12,
        retries=0,
    )
    places = []
    seen = set()
    for feature in payload.get("features") or []:
        props = feature.get("properties") or {}
        coordinates = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coordinates) < 2:
            continue
        label = place_label([props.get("name") or props.get("city") or props.get("street"), props.get("state"), props.get("country")])
        if not label or label in seen:
            continue
        seen.add(label)
        places.append({
            "label": label,
            "lat": float(coordinates[1]),
            "lon": float(coordinates[0]),
        })
    return places


def search_nominatim(text, limit):
    global _nominatim_next
    with _nominatim_lock:
        wait = _nominatim_next - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _nominatim_next = time.monotonic() + 1.1
    payload = get_json(
        "https://nominatim.openstreetmap.org/search",
        params={"q": text, "format": "jsonv2", "limit": limit, "addressdetails": 1},
        timeout=12,
        retries=0,
    )
    places = []
    seen = set()
    for item in payload if isinstance(payload, list) else []:
        address = item.get("address") or {}
        label = place_label([
            address.get("city") or address.get("town") or address.get("village") or address.get("hamlet") or item.get("name"),
            address.get("state"),
            address.get("country"),
        ])
        if not label or label in seen:
            continue
        seen.add(label)
        places.append({
            "label": label,
            "lat": float(item["lat"]),
            "lon": float(item["lon"]),
        })
    return places


def place_label(parts):
    unique = []
    for part in parts:
        if part and part not in unique:
            unique.append(part)
    return ", ".join(unique)


def fetch_weather(points, forecast_days):
    unique = []
    seen = set()
    for point in points:
        key = weather_key(point["lat"], point["lon"])
        if key in seen:
            continue
        seen.add(key)
        unique.append({"key": key, "lat": point["lat"], "lon": point["lon"]})

    found = {}
    missing = []
    for item in unique:
        cached = cache_get(("wx", item["key"], forecast_days))
        if cached is not None:
            found[item["key"]] = cached
        else:
            missing.append(item)

    def load_batch(batch):
        payload = get_json("https://api.open-meteo.com/v1/forecast", params={
            "latitude": ",".join(f"{item['lat']:.4f}" for item in batch),
            "longitude": ",".join(f"{item['lon']:.4f}" for item in batch),
            "hourly": "rain,snowfall,wind_speed_10m",
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "timezone": "UTC",
            "forecast_days": forecast_days,
        }, timeout=25, retries=2)
        if isinstance(payload, dict) and payload.get("error"):
            raise PlannerError("The weather service rejected the request. Try again.")
        rows = [payload] if isinstance(payload, dict) else payload
        if len(rows) != len(batch):
            raise PlannerError("The weather service returned an unexpected result.")
        loaded = {}
        for item, row in zip(batch, rows):
            parsed = parse_weather_row(row)
            loaded[item["key"]] = parsed
            cache_set(("wx", item["key"], forecast_days), parsed, 20 * 60)
        return loaded

    with ThreadPoolExecutor(max_workers=4) as pool:
        for loaded in pool.map(load_batch, chunks(missing, 20)):
            found.update(loaded)
    return found


def parse_weather_row(row):
    hours = row.get("hourly") or {}
    times = []
    for stamp in hours.get("time") or []:
        times.append(datetime.strptime(stamp, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc))

    def column(name):
        values = hours.get(name) or []
        series = {}
        for moment, value in zip(times, values):
            series[moment] = 0.0 if value is None else float(value)
        return series

    return {
        "wind": column("wind_speed_10m"),
        "rain": column("rain"),
        "snow": column("snowfall"),
    }


def read_conditions(weather, lat, lon, moment):
    row = weather.get(weather_key(lat, lon))
    if not row or not row["wind"]:
        raise PlannerError("Weather was unavailable for a point on the route.")
    moment = moment.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    if moment in row["wind"]:
        return {
            "wind": row["wind"][moment],
            "rain": row["rain"].get(moment, 0.0),
            "snow": row["snow"].get(moment, 0.0),
        }, False

    nearest = min(row["wind"], key=lambda item: abs(item - moment))
    return {
        "wind": row["wind"][nearest],
        "rain": row["rain"].get(nearest, 0.0),
        "snow": row["snow"].get(nearest, 0.0),
    }, True


def risk_hours(row, departure, load_lb):
    start = departure.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    risks = []
    for hour in range(49):
        moment = start + timedelta(hours=hour)
        if moment not in row["wind"]:
            risks.append(None)
            continue
        checked = assess(
            row["wind"][moment],
            row["rain"].get(moment, 0.0),
            row["snow"].get(moment, 0.0),
            load_lb,
        )
        risks.append(checked["risk"])
    return risks


def forecast_window(departure, horizon):
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    hours = (horizon - start).total_seconds() / 3600
    days = int(hours // 24) + 1
    return min(16, max(2, days))


def weather_key(lat, lon):
    return (round(lat, 2), round(lon, 2))


def iso_z(moment):
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def chunks(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def get_json(url, params=None, timeout=25, retries=1):
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        except requests.RequestException as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.4)
            continue
        if response.status_code == 429 or response.status_code >= 500:
            last_error = PlannerError(
                "A map service is busy. Wait a moment and try again."
                if response.status_code == 429
                else "A map or weather service returned an error. Try again."
            )
            if attempt < retries:
                time.sleep(0.6 if response.status_code == 429 else 0.3)
            continue
        if response.status_code >= 400:
            raise PlannerError("A map or weather service returned an error. Try again.")
        try:
            return response.json()
        except ValueError as exc:
            raise PlannerError("A map or weather service returned an unreadable response.") from exc
    if isinstance(last_error, PlannerError):
        raise last_error
    raise PlannerError("A map or weather service did not respond. Try again.") from last_error


def cache_get(key):
    now = time.monotonic()
    with _cache_lock:
        item = _cache.get(key)
        if not item:
            return None
        expires, value = item
        if expires < now:
            _cache.pop(key, None)
            return None
        return value


def cache_set(key, value, ttl):
    now = time.monotonic()
    with _cache_lock:
        if len(_cache) > 500:
            expired = [item for item, (expires, _value) in _cache.items() if expires < now]
            for item in expired:
                _cache.pop(item, None)
        if len(_cache) > 500:
            _cache.clear()
        _cache[key] = (now + ttl, value)
