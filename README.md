# SafeRoute

A small React and Django app that helps a truck driver compare three routes. It checks the weather along each route, applies load-weight rules, and recommends the safest option.

## Run it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend && python manage.py test && python manage.py runserver
```

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

Vite forwards `/api` to Django on port 8000. The page asks for places, roads, and weather from the browser, then Django scores the routes.

## What you enter

- Origin and destination. Start typing and pick a place from the list.
- Departure date and time
- Load weight in pounds

## What it does

1. Geocodes both places. Photon is asked first, then Open-Meteo.
2. Asks OSRM for up to three driving routes. If the router returns fewer distinct roads, the app requests nearby detours so there are still choices.
3. Samples weather along every route. Trips under 200 miles are checked every 10 miles, trips under 600 miles every 25 miles, and longer trips every 50 miles. All three options in one search use the same spacing, taken from the most direct route.
4. Estimates the arrival time at each check from the route duration, then reads wind, rain, and snow for that hour from Open-Meteo.
5. Scores each check and recommends a route.

The map draws the selected route in risk colors, the other routes in their own colors, and a heatmap of corridor risk. The slider moves that heatmap from the departure hour through 48 hours. Route scores stay tied to arrival time at each checkpoint.

## Risk rules

A checkpoint takes the worst of wind, rain, and snow.

| | Low | Moderate | High | Severe | No travel |
| --- | --- | --- | --- | --- | --- |
| Wind | below 25 mph | 25 up to 35 | 35 up to 45 | 45 up to 55 | 55 and above |
| Rain | below 0.10 in/hr | 0.10 up to 0.25 | 0.25 up to 0.50 | 0.50 through 1.00 | above 1.00 |
| Snow | below 0.5 in/hr | 0.5 up to 1.0 | 1.0 up to 2.0 | 2.0 through 3.0 | above 3.0 |

Load changes the wind score:

- 55 mph or more is no-travel for any load
- 45–54 mph with a load over 30,000 lb is no-travel
- 35–44 mph with a load over 40,000 lb is severe

Miles between two checks count at the worse of the two ends. Average risk is that score weighted by miles (low = 1 … no-travel = 5).

Recommendation order:

1. Fewest no-travel miles
2. Fewest severe miles
3. Fewest high miles
4. Lowest average risk
5. Shortest travel time

No-travel miles are ranked first because those stretches should not be driven.

## API

`POST /api/plan`

```json
{
  "origin": "Chicago, IL",
  "destination": "Indianapolis, IN",
  "departure": "2026-10-16T18:00:00Z",
  "load_lb": 42000
}
```

Routing, geocoding, and weather come from public services. No API key is required.
