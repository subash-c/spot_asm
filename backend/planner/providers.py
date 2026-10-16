"""HTTP clients for geocoding, routing, and weather providers."""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from math import isfinite

import requests

from .errors import PlannerError

logger = logging.getLogger(__name__)

USER_AGENT = "SafeRoute/1.0 (truck route planner demo)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}
METERS_PER_MILE = 1609.344
ROUTERS = (
    "https://routing.openstreetmap.de/routed-car",
    "https://router.project-osrm.org",
)
WEATHER_BATCH_SIZE = 20

_preferred_router = ROUTERS[0]
_router_lock = threading.Lock()
_cache = {}
_cache_lock = threading.Lock()
_nominatim_lock = threading.Lock()
_nominatim_next = 0.0


def request_osrm(places, alternatives):
    key = (
        "osrm",
        tuple((round(lat, 4), round(lon, 4)) for lat, lon in places),
        int(alternatives or 0),
    )
    cached = cache_get(key)
    if cached is not None:
        return [dict(route) for route in cached]

    coordinates = ";".join(
        f"{lon:.5f},{lat:.5f}" for lat, lon in places
    )
    params = {
        "alternatives": alternatives if alternatives else "false",
        "overview": "full",
        "geometries": "geojson",
        "steps": "false",
    }
    payload = None
    last_error = None
    for router in router_order():
        try:
            payload = get_json(
                f"{router}/route/v1/driving/{coordinates}",
                params=params,
                timeout=20,
                retries=0,
            )
            remember_router(router)
            break
        except PlannerError as exc:
            logger.warning("router %s failed: %s", router, exc)
            last_error = exc

    if payload is None:
        time.sleep(0.7)
        router = router_order()[0]
        try:
            payload = get_json(
                f"{router}/route/v1/driving/{coordinates}",
                params=params,
                timeout=20,
                retries=0,
            )
        except PlannerError:
            raise last_error or PlannerError(
                "A map service is busy. Wait a moment and try again."
            )

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
    return (preferred,) + tuple(
        router for router in ROUTERS if router != preferred
    )


def remember_router(router):
    global _preferred_router
    with _router_lock:
        _preferred_router = router


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
        properties = feature.get("properties") or {}
        coordinates = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coordinates) < 2:
            continue
        label = place_label([
            properties.get("name")
            or properties.get("city")
            or properties.get("street"),
            properties.get("state"),
            properties.get("country"),
        ])
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
        params={
            "q": text,
            "format": "jsonv2",
            "limit": limit,
            "addressdetails": 1,
        },
        timeout=12,
        retries=0,
    )
    places = []
    seen = set()
    for item in payload if isinstance(payload, list) else []:
        address = item.get("address") or {}
        label = place_label([
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("hamlet")
            or item.get("name"),
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
    unique = unique_points(points)
    found = {}
    missing = []
    for item in unique:
        cached = cache_get(("weather", item["key"], forecast_days))
        if cached is not None:
            found[item["key"]] = cached
        else:
            missing.append(item)

    def load_batch(batch):
        payload = get_json(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": ",".join(
                    f"{item['lat']:.4f}" for item in batch
                ),
                "longitude": ",".join(
                    f"{item['lon']:.4f}" for item in batch
                ),
                "hourly": "rain,snowfall,wind_speed_10m",
                "wind_speed_unit": "mph",
                "precipitation_unit": "inch",
                "timezone": "UTC",
                "forecast_days": forecast_days,
            },
            timeout=25,
            retries=2,
        )
        if isinstance(payload, dict) and payload.get("error"):
            raise PlannerError(
                "The weather service rejected the request. Try again."
            )
        rows = [payload] if isinstance(payload, dict) else payload
        if len(rows) != len(batch):
            raise PlannerError(
                "The weather service returned an unexpected result."
            )

        loaded = {}
        for item, row in zip(batch, rows):
            parsed = parse_weather_row(row)
            loaded[item["key"]] = parsed
            cache_set(
                ("weather", item["key"], forecast_days),
                parsed,
                20 * 60,
            )
        return loaded

    with ThreadPoolExecutor(max_workers=4) as pool:
        for loaded in pool.map(
            load_batch,
            chunks(missing, WEATHER_BATCH_SIZE),
        ):
            found.update(loaded)
    return found


def unique_points(points):
    unique = []
    seen = set()
    for point in points:
        key = weather_key(point["lat"], point["lon"])
        if key in seen:
            continue
        seen.add(key)
        unique.append({
            "key": key,
            "lat": point["lat"],
            "lon": point["lon"],
        })
    return unique


def parse_weather_row(row):
    if not isinstance(row, dict) or not isinstance(row.get("hourly"), dict):
        raise PlannerError(
            "The weather service returned an unexpected result."
        )
    hourly = row["hourly"]
    stamps = hourly.get("time")
    if not isinstance(stamps, list) or not stamps or len(stamps) > 400:
        raise PlannerError(
            "The weather service returned an unexpected result."
        )

    try:
        times = [
            datetime.strptime(stamp, "%Y-%m-%dT%H:%M").replace(
                tzinfo=timezone.utc,
            )
            for stamp in stamps
        ]
    except (TypeError, ValueError) as exc:
        raise PlannerError(
            "The weather service returned an unexpected result."
        ) from exc

    def column(name):
        values = hourly.get(name)
        if not isinstance(values, list) or len(values) != len(times):
            raise PlannerError(
                "The weather service returned an unexpected result."
            )
        series = {}
        try:
            for moment, value in zip(times, values):
                number = 0.0 if value is None else float(value)
                if not isfinite(number):
                    raise ValueError
                series[moment] = number
        except (TypeError, ValueError) as exc:
            raise PlannerError(
                "The weather service returned an unexpected result."
            ) from exc
        return series

    return {
        "wind": column("wind_speed_10m"),
        "rain": column("rain"),
        "snow": column("snowfall"),
    }


def weather_key(lat, lon):
    return (round(lat, 2), round(lon, 2))


def chunks(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def get_json(url, params=None, timeout=25, retries=1):
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=timeout,
            )
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
            raise PlannerError(
                "A map or weather service returned an error. Try again."
            )
        try:
            return response.json()
        except ValueError as exc:
            raise PlannerError(
                "A map or weather service returned an unreadable response."
            ) from exc

    if isinstance(last_error, PlannerError):
        raise last_error
    raise PlannerError(
        "A map or weather service did not respond. Try again."
    ) from last_error


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
            expired = [
                item
                for item, (expires, _value) in _cache.items()
                if expires < now
            ]
            for item in expired:
                _cache.pop(item, None)
        if len(_cache) > 500:
            _cache.clear()
        _cache[key] = (now + ttl, value)
