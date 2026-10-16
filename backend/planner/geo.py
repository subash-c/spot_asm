"""Small geometry helpers for route sampling. Distances are in miles."""

from math import asin, atan2, cos, degrees, radians, sin, sqrt

EARTH_MILES = 3958.7613


def haversine_miles(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * EARTH_MILES * asin(min(1.0, sqrt(a)))


def polyline_length(coordinates):
    total = 0.0
    lon, lat = coordinates[0]
    for next_lon, next_lat in coordinates[1:]:
        total += haversine_miles(lat, lon, next_lat, next_lon)
        lon, lat = next_lon, next_lat
    return total


def weather_interval_miles(distance_miles):
    """10 miles under 200, 25 miles under 600, otherwise 50."""
    if distance_miles < 200:
        return 10
    if distance_miles < 600:
        return 25
    return 50


def sample_polyline(coordinates, interval_miles):
    """Walk [lon, lat] coordinates and return (lat, lon, mile) samples."""
    if interval_miles <= 0:
        raise ValueError("Sample interval must be positive.")
    if len(coordinates) < 2:
        raise ValueError("Route geometry is too short.")

    lon0, lat0 = coordinates[0]
    samples = [(lat0, lon0, 0.0)]
    next_mile = float(interval_miles)
    walked = 0.0
    prev_lat, prev_lon = lat0, lon0

    for lon, lat in coordinates[1:]:
        segment = haversine_miles(prev_lat, prev_lon, lat, lon)
        if segment <= 0:
            prev_lat, prev_lon = lat, lon
            continue

        start = walked
        guard = 0
        while next_mile <= start + segment + 1e-6:
            guard += 1
            if guard > 10000:
                break
            ratio = min(1.0, max(0.0, (next_mile - start) / segment))
            samples.append((
                prev_lat + (lat - prev_lat) * ratio,
                prev_lon + (lon - prev_lon) * ratio,
                next_mile,
            ))
            next_mile += interval_miles
            if ratio >= 1:
                break

        walked = start + segment
        prev_lat, prev_lon = lat, lon

    if abs(samples[-1][2] - walked) <= 0.25:
        samples[-1] = (prev_lat, prev_lon, walked)
    elif walked - samples[-1][2] > 0.25:
        samples.append((prev_lat, prev_lon, walked))

    cleaned = [samples[0]]
    for sample in samples[1:]:
        if sample[2] - cleaned[-1][2] < 0.05:
            cleaned[-1] = sample
        else:
            cleaned.append(sample)
    return cleaned


def point_at_distance(coordinates, target_miles):
    lon, lat = coordinates[0]
    if target_miles <= 0:
        return lat, lon

    walked = 0.0
    prev_lat, prev_lon = lat, lon
    for next_lon, next_lat in coordinates[1:]:
        segment = haversine_miles(prev_lat, prev_lon, next_lat, next_lon)
        if segment > 0 and walked + segment >= target_miles:
            ratio = (target_miles - walked) / segment
            return (
                prev_lat + (next_lat - prev_lat) * ratio,
                prev_lon + (next_lon - prev_lon) * ratio,
            )
        walked += segment
        prev_lat, prev_lon = next_lat, next_lon
    return prev_lat, prev_lon


def bearing_deg(lat1, lon1, lat2, lon2):
    lat1, lat2 = radians(lat1), radians(lat2)
    dlon = radians(lon2 - lon1)
    x = sin(dlon) * cos(lat2)
    y = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(dlon)
    return (degrees(atan2(x, y)) + 360) % 360


def destination_point(lat, lon, bearing, distance_miles):
    angular = distance_miles / EARTH_MILES
    heading = radians(bearing)
    lat1 = radians(lat)
    lon1 = radians(lon)
    lat2 = asin(sin(lat1) * cos(angular) + cos(lat1) * sin(angular) * cos(heading))
    lon2 = lon1 + atan2(
        sin(heading) * sin(angular) * cos(lat1),
        cos(angular) - sin(lat1) * sin(lat2),
    )
    return degrees(lat2), (degrees(lon2) + 540) % 360 - 180


def shape_points(coordinates, fractions=(0.2, 0.4, 0.6, 0.8)):
    length = polyline_length(coordinates)
    return [point_at_distance(coordinates, length * fraction) for fraction in fractions]


def mean_separation(points_a, points_b):
    total = 0.0
    for (lat1, lon1), (lat2, lon2) in zip(points_a, points_b):
        total += haversine_miles(lat1, lon1, lat2, lon2)
    return total / len(points_a)


def scale_miles(samples, official_miles):
    geometric = samples[-1][2] or 1.0
    factor = official_miles / geometric
    return [(lat, lon, mile * factor) for lat, lon, mile in samples]


def group_segments(samples, risk_at_mile):
    """Join nearby samples that share a risk into map polylines."""
    segments = []
    for lat, lon, mile in samples:
        risk = risk_at_mile(mile)
        point = [round(lat, 5), round(lon, 5)]
        if not segments or segments[-1]["risk"] != risk:
            if segments:
                segments[-1]["path"].append(point)
            segments.append({"risk": risk, "path": [point]})
        else:
            segments[-1]["path"].append(point)
    return segments


def corridor_points(coordinate_lists, spacing_miles=22, radius_miles=30, limit=48):
    """Points along the band around the routes, for the forecast heatmap."""
    anchors = []
    for coordinates in coordinate_lists:
        if len(coordinates) < 2:
            continue
        for lat, lon, _mile in sample_polyline(coordinates, 30):
            anchors.append((lat, lon))
    if not anchors:
        return []

    lats = [lat for lat, _lon in anchors]
    lons = [lon for _lat, lon in anchors]
    south, north = min(lats), max(lats)
    west, east = min(lons), max(lons)
    mid_lat = (south + north) / 2
    cos_lat = max(0.25, cos(radians(mid_lat)))

    lat_step = max(spacing_miles / 69.0, (north - south) / 18 or 0.2)
    lon_step = max(spacing_miles / (69.0 * cos_lat), (east - west) / 18 or 0.2)
    keep_within = max(radius_miles, spacing_miles * 0.9)

    points = []
    lat = south
    row = 0
    while lat <= north + 1e-9 and row < 24:
        lon = west
        col = 0
        while lon <= east + 1e-9 and col < 24:
            nearest = min(haversine_miles(lat, lon, a_lat, a_lon) for a_lat, a_lon in anchors)
            if nearest <= keep_within:
                points.append({"lat": round(lat, 4), "lon": round(lon, 4)})
            lon += lon_step
            col += 1
        lat += lat_step
        row += 1

    if len(points) < 4:
        points = [{"lat": round(lat, 4), "lon": round(lon, 4)} for lat, lon in anchors]

    if len(points) > limit:
        stride = len(points) / limit
        points = [points[int(index * stride)] for index in range(limit)]
    return points
