"""Build, validate, and score weather-aware route plans."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from math import isfinite

from .errors import PlannerError
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
from .providers import (
    METERS_PER_MILE,
    cache_get,
    cache_set,
    chunks,
    fetch_weather,
    geocode,
    parse_weather_row,
    request_osrm,
    unique_points,
    weather_key,
)
from .risk import assess, rank_routes, summarize_checkpoints, worst

NAMES = ("Route A", "Route B", "Route C")
MAX_ROUTE_MILES = 4000
MAX_ROUTE_DURATION_SECONDS = 14 * 24 * 60 * 60
MAX_ROUTE_POINTS = 15000
MAX_ENDPOINT_GAP_MILES = 20


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
    weather_points, forecast_days = weather_inputs(routes, departure)
    weather = fetch_weather(weather_points, forecast_days)
    result = score_loaded(origin, destination, departure, load_lb, routes, weather)
    cache_set(plan_key, result, 10 * 60)
    return result


def client_prepare(origin, destination, departure, payloads):
    """Turn browser-fetched OSRM answers into the next requests the page should make."""
    if haversine_miles(origin["lat"], origin["lon"], destination["lat"], destination["lon"]) < 1:
        raise PlannerError("Origin and destination are the same place.")
    if not isinstance(payloads, list) or not payloads or len(payloads) > 6:
        raise PlannerError("Could not read the driving routes.")

    bundles = [parse_osrm_payload(payload) for payload in payloads]
    if not bundles[0]:
        raise PlannerError("No driving route was found between those places.")
    bundles[0].sort(key=lambda route: route["duration_seconds"])
    candidates = [
        route
        for bundle in bundles
        for route in bundle
        if route_matches_trip(route, origin, destination)
    ]
    if not candidates:
        raise PlannerError("No driving route was found between those places.")

    if len(payloads) == 1 and len(pick_three(candidates)) < 3:
        start = {"lat": origin["lat"], "lon": origin["lon"]}
        end = {"lat": destination["lat"], "lon": destination["lon"]}
        calls = []
        for lat, lon in via_points(bundles[0][0])[:3]:
            calls.append([start, {"lat": lat, "lon": lon}, end])
        if calls:
            return {"status": "via", "calls": calls}

    chosen = pick_three(candidates)[:3]
    if not chosen:
        raise PlannerError("No driving route was found between those places.")
    batches, forecast_days = weather_inputs(chosen, departure, batched=True)
    return {
        "status": "ready",
        "routes": chosen,
        "batches": batches,
        "forecast_days": forecast_days,
    }


def client_finish(origin, destination, departure, load_lb, routes, payloads):
    routes = clean_client_routes(routes)
    if any(not route_matches_trip(route, origin, destination) for route in routes):
        raise PlannerError("The driving routes do not match the selected trip.")
    batches, _forecast_days = weather_inputs(routes, departure, batched=True)
    if not isinstance(payloads, list) or len(payloads) != len(batches):
        raise PlannerError("The weather service returned an unexpected result.")
    weather = {}
    for batch, payload in zip(batches, payloads):
        if isinstance(payload, dict) and payload.get("error"):
            raise PlannerError("The weather service rejected the request. Try again.")
        rows = [payload] if isinstance(payload, dict) else payload
        if not isinstance(rows, list) or len(rows) != len(batch):
            raise PlannerError("The weather service returned an unexpected result.")
        for item, row in zip(batch, rows):
            weather[weather_key(item["lat"], item["lon"])] = parse_weather_row(row)
    return score_loaded(origin, destination, departure, load_lb, routes, weather)


def weather_inputs(routes, departure, batched=False):
    _interval, horizon, _prepared, _grid, weather_points = collect_trip(routes, departure)
    forecast_days = forecast_window(departure, horizon)
    unique = unique_points(weather_points)
    if not batched:
        return unique, forecast_days
    return [
        [{"lat": item["lat"], "lon": item["lon"]} for item in batch]
        for batch in chunks(unique, 20)
    ], forecast_days


def collect_trip(routes, departure):
    interval = weather_interval_miles(routes[0]["distance_miles"])
    horizon = departure + timedelta(hours=48)
    weather_points = []
    prepared = []
    for route in routes:
        horizon = max(horizon, departure + timedelta(seconds=route["duration_seconds"]))
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
    return interval, horizon, prepared, grid, weather_points


def score_loaded(origin, destination, departure, load_lb, routes, weather):
    interval, _horizon, prepared, grid, _points = collect_trip(routes, departure)
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

    return {
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


def parse_osrm_payload(payload):
    if not isinstance(payload, dict) or payload.get("code") != "Ok":
        return []
    routes = []
    for route in payload.get("routes") or []:
        cleaned = clean_coordinates((route.get("geometry") or {}).get("coordinates"))
        if cleaned is None:
            continue
        try:
            distance = float(route["distance"]) / METERS_PER_MILE
            duration = float(route["duration"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            not isfinite(distance)
            or not isfinite(duration)
            or distance <= 0
            or distance > MAX_ROUTE_MILES
            or duration < 0
            or duration > MAX_ROUTE_DURATION_SECONDS
        ):
            continue
        routes.append({
            "coordinates": cleaned,
            "distance_miles": distance,
            "duration_seconds": duration,
        })
    return routes


def clean_client_routes(routes):
    if not isinstance(routes, list) or not routes or len(routes) > 3:
        raise PlannerError("Could not read the driving routes.")
    cleaned = []
    for route in routes:
        if not isinstance(route, dict):
            raise PlannerError("Could not read the driving routes.")
        coordinates = clean_coordinates(route.get("coordinates"))
        try:
            distance = float(route["distance_miles"])
            duration = float(route["duration_seconds"])
        except (KeyError, TypeError, ValueError):
            raise PlannerError("Could not read the driving routes.")
        if (
            coordinates is None
            or not isfinite(distance)
            or not isfinite(duration)
            or distance <= 0
            or distance > MAX_ROUTE_MILES
            or duration < 0
            or duration > MAX_ROUTE_DURATION_SECONDS
        ):
            raise PlannerError("Could not read the driving routes.")
        cleaned.append({
            "coordinates": coordinates,
            "distance_miles": distance,
            "duration_seconds": duration,
        })
    return cleaned


def clean_coordinates(coordinates):
    if (
        not isinstance(coordinates, list)
        or len(coordinates) < 2
        or len(coordinates) > MAX_ROUTE_POINTS
    ):
        return None
    cleaned = []
    for pair in coordinates:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            return None
        try:
            lon = float(pair[0])
            lat = float(pair[1])
        except (TypeError, ValueError):
            return None
        if (
            not isfinite(lon)
            or not isfinite(lat)
            or not (-180 <= lon <= 180 and -90 <= lat <= 90)
        ):
            return None
        cleaned.append([lon, lat])
    return cleaned


def route_matches_trip(route, origin, destination):
    """Ensure browser-provided geometry starts and ends near the selected places."""
    start_lon, start_lat = route["coordinates"][0]
    end_lon, end_lat = route["coordinates"][-1]
    return (
        haversine_miles(origin["lat"], origin["lon"], start_lat, start_lon)
        <= MAX_ENDPOINT_GAP_MILES
        and haversine_miles(destination["lat"], destination["lon"], end_lat, end_lon)
        <= MAX_ENDPOINT_GAP_MILES
    )


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


def iso_z(moment):
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
