from __future__ import annotations

import math
import os
from dataclasses import dataclass, asdict
from typing import Iterable

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_EMAIL = os.environ.get("NOMINATIM_EMAIL", "").strip()
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
USER_AGENT = os.environ.get(
    "RADIUS_MAP_USER_AGENT",
    "RadiusMapStudentProject/2.0 (set RADIUS_MAP_USER_AGENT in Vercel)",
)


@dataclass
class Place:
    name: str
    latitude: float
    longitude: float
    distance_m: float
    category: str
    address: str = ""


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_m = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return earth_radius_m * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def radius_to_metres(value: float, unit: str, travel_mode: str) -> float:
    if value <= 0:
        raise ValueError("Radius must be greater than zero.")

    if unit == "km":
        metres = value * 1000
    elif unit == "m":
        metres = value
    elif unit == "minutes":
        speeds_kmh = {"walk": 5, "bike": 15, "drive": 40}
        metres = value * speeds_kmh.get(travel_mode, 5) * 1000 / 60
    else:
        raise ValueError("Unsupported radius unit.")

    # Public Overpass instances should not be used for huge searches.
    return min(metres, 20_000)


def geocode(address: str) -> tuple[float, float, str]:
    response = requests.get(
        NOMINATIM_URL,
        params={
            "q": address,
            "format": "jsonv2",
            "limit": 1,
            "addressdetails": 1,
            **({"email": NOMINATIM_EMAIL} if NOMINATIM_EMAIL else {}),
        },
        headers={"User-Agent": USER_AGENT},
        timeout=15,
    )
    response.raise_for_status()
    results = response.json()
    if not results:
        raise ValueError("The address could not be found.")
    result = results[0]
    return float(result["lat"]), float(result["lon"]), result.get("display_name", address)


def build_overpass_query(lat: float, lon: float, radius_m: float) -> str:
    filters = [
        ("amenity", "restaurant|cafe|fast_food|food_court", "Restaurant / café"),
        ("amenity", "coworking_space|library", "Work-friendly place"),
        ("office", "coworking", "Coworking space"),
    ]
    statements: list[str] = []
    for key, values, _ in filters:
        for object_type in ("node", "way", "relation"):
            statements.append(
                f'{object_type}["{key}"~"^({values})$"](around:{int(radius_m)},{lat},{lon});'
            )
    return "[out:json][timeout:25];(" + "".join(statements) + ");out center tags;"


def query_overpass(query: str) -> list[dict]:
    errors: list[str] = []
    for endpoint in OVERPASS_URLS:
        try:
            response = requests.post(
                endpoint,
                data={"data": query},
                headers={"User-Agent": USER_AGENT},
                timeout=35,
            )
            response.raise_for_status()
            return response.json().get("elements", [])
        except requests.RequestException as exc:
            errors.append(str(exc))
    raise RuntimeError("Map data service is temporarily unavailable. " + " | ".join(errors))


def element_coordinates(element: dict) -> tuple[float, float] | None:
    if "lat" in element and "lon" in element:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center")
    if center and "lat" in center and "lon" in center:
        return float(center["lat"]), float(center["lon"])
    return None


def format_osm_address(tags: dict) -> str:
    pieces = [
        tags.get("addr:housenumber", ""),
        tags.get("addr:street", ""),
        tags.get("addr:city", ""),
    ]
    return " ".join(piece for piece in pieces if piece).strip()


def parse_places(elements: Iterable[dict], origin_lat: float, origin_lon: float) -> list[Place]:
    places: list[Place] = []
    seen: set[tuple[str, int, int]] = set()

    for element in elements:
        coords = element_coordinates(element)
        if not coords:
            continue
        lat, lon = coords
        tags = element.get("tags", {})
        amenity = tags.get("amenity", "")
        office = tags.get("office", "")

        if amenity in {"restaurant", "cafe", "fast_food", "food_court"}:
            category = "Restaurant / café"
        elif amenity == "library":
            category = "Library"
        else:
            category = "Coworking space"

        name = tags.get("name") or tags.get("brand") or f"Unnamed {category.lower()}"
        distance = haversine_m(origin_lat, origin_lon, lat, lon)
        key = (name.lower(), round(lat, 5), round(lon, 5))
        if key in seen:
            continue
        seen.add(key)
        places.append(
            Place(
                name=name,
                latitude=lat,
                longitude=lon,
                distance_m=round(distance, 1),
                category=category,
                address=format_osm_address(tags),
            )
        )

    places.sort(key=lambda place: place.distance_m)
    return places[:100]


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/api/search")
def search_places():
    try:
        payload = request.get_json(force=True)
        address = str(payload.get("address", "")).strip()
        radius_value = float(payload.get("radius", 0))
        radius_unit = str(payload.get("unit", "km"))
        travel_mode = str(payload.get("travelMode", "walk"))

        if not address:
            raise ValueError("Enter an address.")

        radius_m = radius_to_metres(radius_value, radius_unit, travel_mode)
        lat, lon, display_name = geocode(address)
        elements = query_overpass(build_overpass_query(lat, lon, radius_m))
        places = parse_places(elements, lat, lon)

        return jsonify(
            {
                "origin": {"latitude": lat, "longitude": lon, "name": display_name},
                "radius_m": round(radius_m),
                "places": [asdict(place) for place in places],
            }
        )
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400
    except requests.RequestException as exc:
        return jsonify({"error": f"Address service error: {exc}"}), 502
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception as exc:
        app.logger.exception("Search failed")
        return jsonify({"error": f"Unexpected error: {exc}"}), 500


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
