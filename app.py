from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import requests
from flask import Flask, jsonify, render_template, request, send_from_directory

# This file can be used either as:
#   app.py
# or:
#   api/index.py
#
# Path handling below finds the project root in both layouts.
CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = (
    CURRENT_FILE.parent.parent
    if CURRENT_FILE.parent.name == "api"
    else CURRENT_FILE.parent
)

app = Flask(
    __name__,
    template_folder=str(PROJECT_ROOT / "templates"),
    static_folder=None,
)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_EMAIL = os.environ.get("NOMINATIM_EMAIL", "").strip()

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

USER_AGENT = os.environ.get(
    "RADIUS_MAP_USER_AGENT",
    "RadiusMapStudentProject/2.0 (contact: 787005@pdsb.net)",
).strip()

MAX_RADIUS_M = 20_000
MAX_RESULTS = 100


@dataclass
class Place:
    name: str
    latitude: float
    longitude: float
    distance_m: float
    category: str
    address: str = ""


def haversine_m(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Return the straight-line distance between two coordinates in metres."""
    earth_radius_m = 6_371_000

    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(p1)
        * math.cos(p2)
        * math.sin(delta_lon / 2) ** 2
    )

    return earth_radius_m * 2 * math.atan2(
        math.sqrt(value),
        math.sqrt(1 - value),
    )


def radius_to_metres(
    value: float,
    unit: str,
    travel_mode: str,
) -> float:
    """Convert metres, kilometres, or travel minutes to metres."""
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Radius must be greater than zero.")

    unit = unit.strip().lower()
    travel_mode = travel_mode.strip().lower()

    if unit == "km":
        metres = value * 1_000
    elif unit == "m":
        metres = value
    elif unit == "minutes":
        speeds_kmh = {
            "walk": 5,
            "bike": 15,
            "drive": 40,
        }
        if travel_mode not in speeds_kmh:
            raise ValueError("Unsupported travel mode.")
        metres = value * speeds_kmh[travel_mode] * 1_000 / 60
    else:
        raise ValueError("Unsupported radius unit.")

    return min(metres, MAX_RADIUS_M)


def geocode(address: str) -> tuple[float, float, str]:
    """Convert an address to coordinates using Nominatim."""
    params: dict[str, object] = {
        "q": address,
        "format": "jsonv2",
        "limit": 1,
        "addressdetails": 1,
    }

    if NOMINATIM_EMAIL:
        params["email"] = NOMINATIM_EMAIL

    response = requests.get(
        NOMINATIM_URL,
        params=params,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
        timeout=15,
    )
    response.raise_for_status()

    results = response.json()
    if not results:
        raise ValueError("The address could not be found.")

    result = results[0]

    try:
        latitude = float(result["lat"])
        longitude = float(result["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("The address service returned invalid coordinates.") from exc

    display_name = str(result.get("display_name") or address)
    return latitude, longitude, display_name


def build_overpass_query(
    latitude: float,
    longitude: float,
    radius_m: float,
) -> str:
    """Build one Overpass query for food and work-friendly places."""
    filters = [
        ("amenity", "restaurant|cafe|fast_food|food_court"),
        ("amenity", "coworking_space|library"),
        ("office", "coworking"),
    ]

    statements: list[str] = []

    for key, values in filters:
        for object_type in ("node", "way", "relation"):
            statements.append(
                f'{object_type}["{key}"~"^({values})$"]'
                f"(around:{int(radius_m)},{latitude},{longitude});"
            )

    return (
        "[out:json][timeout:25];"
        "("
        + "".join(statements)
        + ");"
        "out center tags;"
    )


def query_overpass(query: str) -> list[dict]:
    """Try multiple Overpass endpoints and return matching elements."""
    errors: list[str] = []

    for endpoint in OVERPASS_URLS:
        try:
            response = requests.post(
                endpoint,
                data={"data": query},
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
                timeout=35,
            )
            response.raise_for_status()

            data = response.json()
            elements = data.get("elements", [])

            if not isinstance(elements, list):
                raise RuntimeError("The map service returned invalid data.")

            return elements

        except (
            requests.RequestException,
            ValueError,
            RuntimeError,
        ) as exc:
            errors.append(f"{endpoint}: {exc}")

    raise RuntimeError(
        "Map data service is temporarily unavailable. "
        + " | ".join(errors)
    )


def element_coordinates(
    element: dict,
) -> tuple[float, float] | None:
    """Return coordinates from an Overpass node, way, or relation."""
    if "lat" in element and "lon" in element:
        try:
            return float(element["lat"]), float(element["lon"])
        except (TypeError, ValueError):
            return None

    center = element.get("center")

    if isinstance(center, dict) and "lat" in center and "lon" in center:
        try:
            return float(center["lat"]), float(center["lon"])
        except (TypeError, ValueError):
            return None

    return None


def format_osm_address(tags: dict) -> str:
    """Build a readable address from common OpenStreetMap fields."""
    street_line = " ".join(
        item
        for item in (
            str(tags.get("addr:housenumber", "")).strip(),
            str(tags.get("addr:street", "")).strip(),
        )
        if item
    )

    locality = str(
        tags.get("addr:city")
        or tags.get("addr:town")
        or tags.get("addr:village")
        or ""
    ).strip()

    postcode = str(tags.get("addr:postcode", "")).strip()

    return ", ".join(
        item for item in (street_line, locality, postcode) if item
    )


def parse_places(
    elements: Iterable[dict],
    origin_latitude: float,
    origin_longitude: float,
) -> list[Place]:
    """Convert raw Overpass elements into sorted, deduplicated places."""
    places: list[Place] = []
    seen: set[tuple[str, int, int]] = set()

    for element in elements:
        if not isinstance(element, dict):
            continue

        coordinates = element_coordinates(element)
        if coordinates is None:
            continue

        latitude, longitude = coordinates
        tags = element.get("tags", {})

        if not isinstance(tags, dict):
            tags = {}

        amenity = str(tags.get("amenity", "")).lower()
        office = str(tags.get("office", "")).lower()

        if amenity in {
            "restaurant",
            "cafe",
            "fast_food",
            "food_court",
        }:
            category = "Restaurant / café"
        elif amenity == "library":
            category = "Library"
        elif amenity == "coworking_space" or office == "coworking":
            category = "Coworking space"
        else:
            continue

        name = str(
            tags.get("name")
            or tags.get("brand")
            or f"Unnamed {category.lower()}"
        ).strip()

        distance_m = haversine_m(
            origin_latitude,
            origin_longitude,
            latitude,
            longitude,
        )

        dedupe_key = (
            name.casefold(),
            round(latitude * 100_000),
            round(longitude * 100_000),
        )

        if dedupe_key in seen:
            continue

        seen.add(dedupe_key)

        places.append(
            Place(
                name=name,
                latitude=latitude,
                longitude=longitude,
                distance_m=round(distance_m, 1),
                category=category,
                address=format_osm_address(tags),
            )
        )

    places.sort(key=lambda place: place.distance_m)
    return places[:MAX_RESULTS]


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/style.css")
def stylesheet():
    # Vercel serves public/style.css from its CDN. This route also supports local Flask runs.
    return send_from_directory(PROJECT_ROOT / "public", "style.css", mimetype="text/css")


@app.get("/api/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "service": "radius-map",
        }
    )


@app.post("/api/search")
def search_places():
    try:
        payload = request.get_json(silent=True)

        if not isinstance(payload, dict):
            raise ValueError("Send the request as JSON.")

        address = str(payload.get("address", "")).strip()
        radius_value = float(payload.get("radius", 0))
        radius_unit = str(payload.get("unit", "km")).strip().lower()
        travel_mode = str(
            payload.get("travelMode", "walk")
        ).strip().lower()

        if not address:
            raise ValueError("Enter an address.")

        radius_m = radius_to_metres(
            radius_value,
            radius_unit,
            travel_mode,
        )

        latitude, longitude, display_name = geocode(address)

        overpass_query = build_overpass_query(
            latitude,
            longitude,
            radius_m,
        )

        elements = query_overpass(overpass_query)

        places = parse_places(
            elements,
            latitude,
            longitude,
        )

        return jsonify(
            {
                "origin": {
                    "latitude": latitude,
                    "longitude": longitude,
                    "name": display_name,
                },
                "radius_m": round(radius_m),
                "places": [
                    asdict(place)
                    for place in places
                ],
            }
        )

    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400

    except requests.Timeout:
        return jsonify(
            {
                "error": (
                    "The address service timed out. "
                    "Please try again."
                )
            }
        ), 504

    except requests.RequestException as exc:
        app.logger.warning("Address request failed: %s", exc)
        return jsonify(
            {
                "error": (
                    "The address service is temporarily unavailable."
                )
            }
        ), 502

    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503

    except Exception:
        app.logger.exception("Search failed")
        return jsonify(
            {
                "error": (
                    "An unexpected server error occurred."
                )
            }
        ), 500


# Used for local development. Vercel imports the `app` object directly.
if __name__ == "__main__":
    app.run(
        debug=True,
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "5000")),
    )
