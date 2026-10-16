import json
from datetime import datetime, timedelta, timezone
from django.test import SimpleTestCase, override_settings

from planner.geo import (
    corridor_points,
    destination_point,
    haversine_miles,
    polyline_length,
    sample_polyline,
    weather_interval_miles,
)
from planner.risk import assess, rain_level, rank_routes, snow_level, summarize_checkpoints, wind_level
from planner.providers import place_label
from planner.services import pick_three


class RiskTests(SimpleTestCase):
    def test_wind_thresholds_and_load(self):
        cases = [
            (24.9, 80000, "low"),
            (25, 80000, "moderate"),
            (34.9, 80000, "moderate"),
            (35, 1000, "high"),
            (35, 40000, "high"),
            (35, 40001, "severe"),
            (44.9, 50000, "severe"),
            (45, 30000, "severe"),
            (45, 30001, "no_travel"),
            (54.9, 100, "severe"),
            (55, 100, "no_travel"),
            (70, 0, "no_travel"),
        ]
        for mph, load, expected in cases:
            self.assertEqual(wind_level(mph, load), expected, f"{mph} mph at {load} lb")

    def test_rain_and_snow_thresholds(self):
        self.assertEqual(rain_level(0.09), "low")
        self.assertEqual(rain_level(0.10), "moderate")
        self.assertEqual(rain_level(0.25), "high")
        self.assertEqual(rain_level(0.50), "severe")
        self.assertEqual(rain_level(1.00), "severe")
        self.assertEqual(rain_level(1.01), "no_travel")

        self.assertEqual(snow_level(0.49), "low")
        self.assertEqual(snow_level(0.5), "moderate")
        self.assertEqual(snow_level(1), "high")
        self.assertEqual(snow_level(2), "severe")
        self.assertEqual(snow_level(3), "severe")
        self.assertEqual(snow_level(3.01), "no_travel")

    def test_checkpoint_uses_the_worst_condition(self):
        result = assess(10, 0.6, 0.2, 10000)
        self.assertEqual(result["risk"], "severe")
        self.assertEqual(result["drivers"], ["rain"])
        self.assertEqual(result["note"], "")

    def test_load_note_only_when_the_load_changes_wind(self):
        heavy = assess(40, 0, 0, 45000)
        self.assertEqual(heavy["risk"], "severe")
        self.assertIn("40,000", heavy["note"])

        light = assess(40, 0, 0, 10000)
        self.assertEqual(light["risk"], "high")
        self.assertEqual(light["note"], "")

    def test_miles_use_the_worse_end_of_each_stretch(self):
        checkpoints = [
            {"mile": 0, "risk": "low"},
            {"mile": 10, "risk": "severe"},
            {"mile": 30, "risk": "low"},
        ]
        summary = summarize_checkpoints(checkpoints)
        self.assertEqual(summary["severe_miles"], 30)
        self.assertEqual(summary["low_miles"], 0)
        self.assertEqual(summary["average_risk"], 4)

    def test_rank_prefers_safer_then_shorter(self):
        routes = [
            route("bad", no_travel=5, severe=0, high=0, average=1, seconds=10),
            route("severe", no_travel=0, severe=30, high=0, average=3, seconds=10),
            route("better", no_travel=0, severe=10, high=50, average=2, seconds=99),
            route("best", no_travel=0, severe=10, high=40, average=3, seconds=80),
            route("calmer", no_travel=0, severe=10, high=40, average=1.5, seconds=200),
            route("fast", no_travel=0, severe=10, high=40, average=1.5, seconds=50),
        ]
        ranked = rank_routes(routes)
        self.assertEqual(
            [item["id"] for item in ranked],
            ["fast", "calmer", "best", "better", "severe", "bad"],
        )
        self.assertTrue(ranked[0]["recommended"])
        self.assertEqual(ranked[0]["rank"], 1)


class GeoTests(SimpleTestCase):
    def test_sample_spacing(self):
        coordinates = [[0, index / 100] for index in range(101)]
        samples = sample_polyline(coordinates, 10)
        miles = [mile for _lat, _lon, mile in samples]
        gaps = [b - a for a, b in zip(miles, miles[1:])]
        self.assertEqual(miles[0], 0)
        for gap in gaps[:-1]:
            self.assertAlmostEqual(gap, 10, delta=0.2)
        self.assertLessEqual(gaps[-1], 10.2)
        self.assertAlmostEqual(miles[-1], polyline_length(coordinates), delta=0.2)

    def test_interval_tiers(self):
        self.assertEqual(weather_interval_miles(50), 10)
        self.assertEqual(weather_interval_miles(199), 10)
        self.assertEqual(weather_interval_miles(200), 25)
        self.assertEqual(weather_interval_miles(599), 25)
        self.assertEqual(weather_interval_miles(600), 50)

    def test_corridor_follows_the_route(self):
        coordinates = [[-87.6, 41.9], [-86.2, 39.8]]
        points = corridor_points([coordinates])
        self.assertGreaterEqual(len(points), 4)
        self.assertLessEqual(len(points), 48)

    def test_offset_distance(self):
        lat, lon = destination_point(41.8, -87.6, 90, 60)
        self.assertAlmostEqual(haversine_miles(41.8, -87.6, lat, lon), 60, delta=0.5)

    def test_pick_three_drops_a_near_duplicate(self):
        direct = line(41, 100)
        same = line(41.05, 101)
        north = line(42, 110)
        south = line(40, 120)
        chosen = pick_three([direct, same, north, south])
        self.assertEqual(len(chosen), 3)
        self.assertNotIn(same, chosen)


class ApiTests(SimpleTestCase):
    def test_health(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_plan_requires_post(self):
        response = self.client.get("/api/plan")
        self.assertEqual(response.status_code, 405)

    def test_plan_rejects_a_non_object_body(self):
        response = self.client.post(
            "/api/plan",
            data="[]",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Send JSON.")

    def test_plan_rejects_a_missing_origin(self):
        response = self.client.post(
            "/api/plan",
            data=json.dumps({
                "destination": "Dallas, TX",
                "departure": "2026-10-17T18:00:00Z",
                "load_lb": 1000,
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("origin", response.json()["error"])

    def test_place_search_waits_for_two_letters(self):
        response = self.client.get("/api/places", {"q": "a"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["places"], [])

    @override_settings(ENABLE_SERVER_PROVIDER_API=False)
    def test_server_provider_endpoints_are_disabled_in_production(self):
        self.assertEqual(self.client.get("/api/places", {"q": "Chicago"}).status_code, 404)
        self.assertEqual(
            self.client.post(
                "/api/plan",
                data="{}",
                content_type="application/json",
            ).status_code,
            404,
        )

    def test_place_label_includes_state_and_country(self):
        label = place_label(["Chicago", "Illinois", "United States"])
        self.assertEqual(label, "Chicago, Illinois, United States")

    def test_router_switches_server_when_the_first_is_busy(self):
        from planner import providers

        calls = []

        def fake(url, params=None, timeout=25, retries=1):
            calls.append(url)
            if providers.ROUTERS[0] in url:
                raise providers.PlannerError("busy")
            return {
                "code": "Ok",
                "routes": [{
                    "distance": 1609.344,
                    "duration": 60,
                    "geometry": {"coordinates": [[-87.6, 41.8], [-86.1, 39.7]]},
                }],
            }

        providers.remember_router(providers.ROUTERS[0])
        providers._cache.clear()
        original = providers.get_json
        providers.get_json = fake
        try:
            routes = providers.request_osrm(
                [(41.8, -87.6), (39.7, -86.1)],
                3,
            )
            again = providers.request_osrm(
                [(41.8, -87.6), (39.7, -86.1)],
                3,
            )
        finally:
            providers.get_json = original
            providers._cache.clear()
            providers.remember_router(providers.ROUTERS[0])
        self.assertEqual(len(routes), 1)
        self.assertEqual(len(again), 1)
        self.assertEqual(len(calls), 2)
        self.assertIn(providers.ROUTERS[1], calls[1])


class BrowserPlanTests(SimpleTestCase):
    def test_browser_fetches_are_scored_without_calling_map_services(self):
        departure = future_departure()
        payload = {
            "code": "Ok",
            "routes": [{
                "distance": 290000,
                "duration": 13000,
                "geometry": {"coordinates": [[-87.63, 41.85], [-86.16, 39.77]]},
            }],
        }
        body = {
            "origin": {"label": "Chicago, Illinois, United States", "lat": 41.85, "lon": -87.63},
            "destination": {"label": "Indianapolis, Indiana, United States", "lat": 39.77, "lon": -86.16},
            "departure": departure,
            "osrm": [payload, {"code": "NoRoute"}],
        }
        prepared = self.client.post("/api/prepare", data=json.dumps(body), content_type="application/json")
        self.assertEqual(prepared.status_code, 200, prepared.content)
        step = prepared.json()
        self.assertEqual(step["status"], "ready")
        self.assertGreaterEqual(len(step["batches"]), 1)
        finished = self.client.post("/api/finish", data=json.dumps({
            "origin": body["origin"],
            "destination": body["destination"],
            "departure": body["departure"],
            "load_lb": 42000,
            "routes": step["routes"],
            "weather": [forecast_rows(len(batch)) for batch in step["batches"]],
        }), content_type="application/json")
        self.assertEqual(finished.status_code, 200, finished.content)
        result = finished.json()
        self.assertEqual(len(result["routes"]), 1)
        self.assertGreater(len(result["routes"][0]["checkpoints"]), 1)
        self.assertIn("heatmap", result)

    def test_finish_rejects_a_route_for_different_places(self):
        response = self.client.post("/api/finish", data=json.dumps({
            "origin": {"label": "Chicago", "lat": 41.85, "lon": -87.63},
            "destination": {"label": "Indianapolis", "lat": 39.77, "lon": -86.16},
            "departure": future_departure(),
            "load_lb": 42000,
            "routes": [{
                "coordinates": [[-122.42, 37.77], [-118.24, 34.05]],
                "distance_miles": 380,
                "duration_seconds": 22000,
            }],
            "weather": [],
        }), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("do not match", response.json()["error"])


def route(name, no_travel, severe, high, average, seconds):
    return {
        "id": name,
        "no_travel_miles": no_travel,
        "severe_miles": severe,
        "high_miles": high,
        "average_risk": average,
        "duration_seconds": seconds,
    }


def line(lat, distance_miles):
    return {
        "coordinates": [[-87, lat], [-86, lat], [-85, lat]],
        "distance_miles": distance_miles,
        "duration_seconds": distance_miles * 60,
    }


def future_departure():
    return (
        datetime.now(timezone.utc) + timedelta(hours=2)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def forecast_rows(count):
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    stamps = [
        (start + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%M")
        for hour in range(80)
    ]
    row = {
        "hourly": {
            "time": stamps,
            "rain": [0.0] * len(stamps),
            "snowfall": [0.0] * len(stamps),
            "wind_speed_10m": [10.0] * len(stamps),
        },
    }
    return row if count == 1 else [row for _ in range(count)]
