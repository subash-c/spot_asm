import json
import logging
from datetime import datetime, timedelta, timezone

from django.http import JsonResponse
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST

from .services import PlannerError, client_finish, client_prepare, plan_trip, search_places

logger = logging.getLogger(__name__)


@require_GET
def health(request):
    return JsonResponse({"ok": True})


@require_GET
def places(request):
    try:
        found = search_places(request.GET.get("q", ""))
    except PlannerError:
        found = []
    return JsonResponse({"places": found})


@require_POST
def plan(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Send JSON."}, status=400)

    try:
        origin = clean_text(body.get("origin"), "origin")
        destination = clean_text(body.get("destination"), "destination")
        departure = clean_departure(body.get("departure"))
        load_lb = clean_load(body.get("load_lb"))
        result = plan_trip(origin, destination, departure, load_lb)
    except PlannerError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception:
        logger.exception("plan failed")
        return JsonResponse(
            {"error": "Something went wrong while planning the trip. Try again."},
            status=500,
        )
    return JsonResponse(result)


@require_POST
def prepare(request):
    body = read_body(request)
    if isinstance(body, JsonResponse):
        return body
    try:
        origin = clean_place(body.get("origin"), "origin")
        destination = clean_place(body.get("destination"), "destination")
        departure = clean_departure(body.get("departure"))
        result = client_prepare(origin, destination, departure, body.get("osrm"))
    except PlannerError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception:
        logger.exception("prepare failed")
        return JsonResponse(
            {"error": "Something went wrong while planning the trip. Try again."},
            status=500,
        )
    return JsonResponse(result)


@require_POST
def finish(request):
    body = read_body(request)
    if isinstance(body, JsonResponse):
        return body
    try:
        origin = clean_place(body.get("origin"), "origin")
        destination = clean_place(body.get("destination"), "destination")
        departure = clean_departure(body.get("departure"))
        load_lb = clean_load(body.get("load_lb"))
        result = client_finish(
            origin, destination, departure, load_lb, body.get("routes"), body.get("weather"),
        )
    except PlannerError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception:
        logger.exception("finish failed")
        return JsonResponse(
            {"error": "Something went wrong while planning the trip. Try again."},
            status=500,
        )
    return JsonResponse(result)


def read_body(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Send JSON."}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({"error": "Send JSON."}, status=400)
    return body


def clean_place(value, field):
    if not isinstance(value, dict):
        raise PlannerError(f"Enter the {field}.")
    try:
        lat = float(value.get("lat"))
        lon = float(value.get("lon"))
    except (TypeError, ValueError):
        raise PlannerError(f"Enter the {field}.")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise PlannerError(f"Enter the {field}.")
    return {"label": clean_text(value.get("label"), field), "lat": lat, "lon": lon}


def clean_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise PlannerError(f"Enter the {field}.")
    if len(value.strip()) > 200:
        raise PlannerError(f"The {field} is too long.")
    return value.strip()


def clean_load(value):
    try:
        load = float(value)
    except (TypeError, ValueError):
        raise PlannerError("Enter a load weight in pounds.")
    if load < 0 or load > 200000:
        raise PlannerError("Load weight should be between 0 and 200,000 lb.")
    return load


def clean_departure(value):
    if not isinstance(value, str):
        raise PlannerError("Enter a departure time.")
    parsed = parse_datetime(value)
    if parsed is None:
        raise PlannerError("Enter a valid departure time.")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if parsed < now - timedelta(hours=3):
        raise PlannerError("Departure time is in the past.")
    if parsed > now + timedelta(days=6):
        raise PlannerError("Pick a departure within the next 6 days so the forecast covers the trip.")
    return parsed
