"""Weather risk for a loaded truck.

Each checkpoint scores wind, rain, and snow. The checkpoint keeps the worst
of the three. Load weight can raise the wind score:

- 55 mph or more is no-travel for every load
- 45–54 mph with a load over 30,000 lb is no-travel
- 35–44 mph with a load over 40,000 lb is severe

Miles between two checkpoints count at the worse of the two ends.
Routes rank by no-travel miles, then severe miles, then high miles,
then average risk, then travel time.
"""

LEVELS = ("low", "moderate", "high", "severe", "no_travel")

SCORE = {
    "low": 1,
    "moderate": 2,
    "high": 3,
    "severe": 4,
    "no_travel": 5,
}


def worst(*levels):
    return max(levels, key=lambda level: SCORE[level])


def wind_level(mph, load_lb):
    if mph >= 55:
        return "no_travel"
    if mph >= 45:
        if load_lb > 30000:
            return "no_travel"
        return "severe"
    if mph >= 35:
        if load_lb > 40000:
            return "severe"
        return "high"
    if mph >= 25:
        return "moderate"
    return "low"


def rain_level(inches_per_hour):
    if inches_per_hour > 1:
        return "no_travel"
    if inches_per_hour >= 0.5:
        return "severe"
    if inches_per_hour >= 0.25:
        return "high"
    if inches_per_hour >= 0.1:
        return "moderate"
    return "low"


def snow_level(inches_per_hour):
    if inches_per_hour > 3:
        return "no_travel"
    if inches_per_hour >= 2:
        return "severe"
    if inches_per_hour >= 1:
        return "high"
    if inches_per_hour >= 0.5:
        return "moderate"
    return "low"


def assess(wind_mph, rain_in_hr, snow_in_hr, load_lb):
    wind_mph = round(float(wind_mph or 0), 1)
    rain_in_hr = round(float(rain_in_hr or 0), 2)
    snow_in_hr = round(float(snow_in_hr or 0), 2)

    base_wind = wind_level(wind_mph, 0)
    wind = wind_level(wind_mph, load_lb)
    rain = rain_level(rain_in_hr)
    snow = snow_level(snow_in_hr)
    risk = worst(wind, rain, snow)

    note = ""
    if wind != base_wind and wind == "no_travel":
        note = "Wind 45–54 mph with a load over 30,000 lb is no-travel."
    elif wind != base_wind and wind == "severe":
        note = "Wind 35–44 mph with a load over 40,000 lb is severe."

    drivers = []
    if risk != "low":
        for name, level in (("wind", wind), ("rain", rain), ("snow", snow)):
            if level == risk:
                drivers.append(name)

    return {
        "wind_mph": wind_mph,
        "rain_in_hr": rain_in_hr,
        "snow_in_hr": snow_in_hr,
        "wind_risk": wind,
        "rain_risk": rain,
        "snow_risk": snow,
        "risk": risk,
        "note": note,
        "drivers": drivers,
    }


def summarize_checkpoints(checkpoints):
    totals = {level: 0.0 for level in LEVELS}
    weighted = 0.0
    distance = 0.0

    for prev, current in zip(checkpoints, checkpoints[1:]):
        span = max(0.0, current["mile"] - prev["mile"])
        risk = worst(prev["risk"], current["risk"])
        totals[risk] += span
        weighted += SCORE[risk] * span
        distance += span

    if distance:
        average = weighted / distance
    elif checkpoints:
        average = sum(SCORE[item["risk"]] for item in checkpoints) / len(checkpoints)
    else:
        average = 1.0

    return {
        "no_travel_miles": round(totals["no_travel"], 1),
        "severe_miles": round(totals["severe"], 1),
        "high_miles": round(totals["high"], 1),
        "moderate_miles": round(totals["moderate"], 1),
        "low_miles": round(totals["low"], 1),
        "average_risk": round(average, 2),
    }


def rank_routes(routes):
    ranked = sorted(
        routes,
        key=lambda route: (
            round(route["no_travel_miles"], 1),
            round(route["severe_miles"], 1),
            round(route["high_miles"], 1),
            round(route["average_risk"], 2),
            route["duration_seconds"],
        ),
    )
    for index, route in enumerate(ranked, start=1):
        route["rank"] = index
        route["recommended"] = index == 1
    return ranked
