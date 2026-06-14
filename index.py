from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass, field

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

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

# Age rules: (min_age, max_age or None, label)
CATEGORY_AGE_RULES = {
    "Restaurant / café": (0, None, None),
    "Library": (0, None, None),
    "Coworking space": (16, None, "16+"),
    "Bar / pub": (19, None, "19+"),  # Ontario legal age
}

HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Radius Map</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
:root{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#172033;background:#eef2f7}
*{box-sizing:border-box}body{margin:0}
.app-shell{display:grid;grid-template-columns:390px 1fr;height:100vh}
.panel{overflow-y:auto;padding:30px;background:rgba(255,255,255,.97);box-shadow:12px 0 32px rgba(18,35,64,.12);z-index:500}
#map{height:100vh;width:100%}
.eyebrow{color:#4f46e5;font-size:.72rem;font-weight:800;letter-spacing:.15em;margin:0 0 8px}
h1{margin:0;font-size:2.35rem;letter-spacing:-.045em}.subtitle{color:#64748b;line-height:1.55}
form{margin-top:26px}label{display:block;font-weight:700;font-size:.86rem;margin:15px 0 7px}
input,select{width:100%;border:1px solid #d8dee9;border-radius:12px;padding:12px 13px;font:inherit;background:white}
input:focus,select:focus{outline:3px solid rgba(79,70,229,.15);border-color:#4f46e5}
.form-row{display:grid;grid-template-columns:1fr 1.15fr;gap:12px}small{color:#718096;line-height:1.4}
button{margin-top:20px;width:100%;border:0;border-radius:13px;padding:13px;font:inherit;font-weight:800;color:white;background:#4f46e5;cursor:pointer;box-shadow:0 8px 18px rgba(79,70,229,.24)}
button:hover{background:#4338ca}button:disabled{opacity:.55;cursor:wait}
.status{margin-top:18px;border-radius:11px;font-size:.87rem;line-height:1.4}
.status.loading,.status.success,.status.error{padding:11px 12px}
.status.loading{background:#eef2ff;color:#3730a3}.status.success{background:#ecfdf5;color:#047857}.status.error{background:#fff1f2;color:#be123c}
.results{margin-top:18px}.results-heading{display:flex;justify-content:space-between;align-items:center;color:#64748b;font-size:.82rem;margin-bottom:10px}
.results-heading strong{color:#172033;font-size:.95rem}
.place-card{display:grid;grid-template-columns:34px 1fr;gap:10px;border:1px solid #e5e9f0;border-radius:14px;padding:13px;margin-bottom:10px;cursor:pointer;transition:transform .15s,box-shadow .15s}
.place-card:hover{transform:translateY(-1px);box-shadow:0 7px 18px rgba(18,35,64,.09)}
.place-card.age-blocked{opacity:.45;border-color:#fecaca;background:#fff5f5;cursor:not-allowed}
.place-card.age-blocked:hover{transform:none;box-shadow:none}
.rank{width:28px;height:28px;display:grid;place-items:center;border-radius:9px;background:#eef2ff;color:#4338ca;font-weight:800}
.place-card.age-blocked .rank{background:#fee2e2;color:#b91c1c}
.place-card h2{font-size:.96rem;margin:0 0 4px}.place-card p{color:#526078;font-size:.82rem;margin:0 0 4px}.empty{color:#64748b}
.age-badge{display:inline-block;font-size:.7rem;font-weight:800;padding:2px 7px;border-radius:99px;margin-left:6px;vertical-align:middle}
.age-badge.ok{background:#dcfce7;color:#15803d}
.age-badge.blocked{background:#fee2e2;color:#b91c1c}
.emoji-marker span{display:grid;place-items:center;width:34px;height:34px;border-radius:50%;background:white;box-shadow:0 3px 12px rgba(0,0,0,.25);font-size:18px}
@media(max-width:780px){.app-shell{grid-template-columns:1fr;grid-template-rows:auto 55vh;height:auto}.panel{max-height:none;padding:22px}#map{height:55vh}}
  </style>
</head>
<body>
  <main class="app-shell">
    <aside class="panel">
      <header>
        <p class="eyebrow">PYTHON + OPENSTREETMAP</p>
        <h1>Radius Map</h1>
        <p class="subtitle">Find the closest restaurants, cafés, libraries, and coworking spaces.</p>
      </header>
      <form id="searchForm">
        <label for="address">Starting address</label>
        <input id="address" name="address" type="text" placeholder="e.g. 100 Queen St W, Toronto" required>
        <div class="form-row">
          <div>
            <label for="age">Your age</label>
            <input id="age" name="age" type="number" min="1" max="120" placeholder="e.g. 17" required>
          </div>
          <div>
            <label for="radius">Radius</label>
            <input id="radius" name="radius" type="number" min="0.1" step="0.1" value="2" required>
          </div>
        </div>
        <div class="form-row">
          <div>
            <label for="unit">Unit</label>
            <select id="unit" name="unit">
              <option value="km">Kilometres</option>
              <option value="m">Metres</option>
              <option value="minutes">Minutes</option>
            </select>
          </div>
          <div id="travelModeWrap">
            <label for="travelMode">Travel mode</label>
            <select id="travelMode" name="travelMode">
              <option value="walk">Walking</option>
              <option value="bike">Cycling</option>
              <option value="drive">Driving</option>
            </select>
          </div>
        </div>
        <div id="travelModeNote" hidden><small>Minute searches use estimated speed, not live routing.</small></div>
        <button type="submit" id="searchButton">Search nearby</button>
      </form>
      <div id="status" class="status" aria-live="polite"></div>
      <section id="results" class="results"></section>
    </aside>
    <section id="map" aria-label="Interactive map"></section>
  </main>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const form=document.getElementById('searchForm'),unit=document.getElementById('unit'),
      travelModeNote=document.getElementById('travelModeNote'),
      statusBox=document.getElementById('status'),resultsBox=document.getElementById('results'),
      button=document.getElementById('searchButton');
    const map=L.map('map').setView([43.6532,-79.3832],12);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; OpenStreetMap contributors'}).addTo(map);
    let resultLayer=L.layerGroup().addTo(map),radiusCircle=null;
    unit.addEventListener('change',()=>{travelModeNote.hidden=unit.value!=='minutes'});
    function escapeHtml(v){return String(v).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":"&#39;",'"':'&quot;'})[c])}
    function formatDistance(m){return m<1000?`${Math.round(m)} m`:`${(m/1000).toFixed(2)} km`}
    function markerIcon(cat,blocked){
      const e=cat==='Restaurant / café'?'🍽️':cat==='Library'?'📚':cat==='Bar / pub'?'🍺':'💻';
      const bg=blocked?'#fee2e2':'white';
      return L.divIcon({className:'emoji-marker',html:`<span style="background:${bg}">${e}</span>`,iconSize:[34,34],iconAnchor:[17,17]});
    }
    function renderResults(data){
      resultLayer.clearLayers();
      if(radiusCircle)map.removeLayer(radiusCircle);
      const origin=[data.origin.latitude,data.origin.longitude];
      L.marker(origin).addTo(resultLayer).bindPopup(`<strong>Start</strong><br>${escapeHtml(data.origin.name)}`);
      radiusCircle=L.circle(origin,{radius:data.radius_m,weight:2,fillOpacity:0.08}).addTo(map);
      data.places.forEach((place,i)=>{
        const marker=L.marker([place.latitude,place.longitude],{icon:markerIcon(place.category,place.age_blocked)})
          .addTo(resultLayer)
          .bindPopup(`<strong>${escapeHtml(place.name)}</strong><br>${escapeHtml(place.category)}<br>${formatDistance(place.distance_m)}${place.age_label?'<br><em>'+escapeHtml(place.age_label)+'</em>':''}`);
        marker.on('click',()=>{document.getElementById(`place-${i}`)?.scrollIntoView({behavior:'smooth',block:'center'})});
      });
      map.fitBounds(radiusCircle.getBounds(),{padding:[25,25]});
      if(!data.places.length){resultsBox.innerHTML='<p class="empty">No matching places were found in this radius.</p>';return}
      const showing=data.places.filter(p=>!p.age_blocked).length;
      const blocked=data.places.length-showing;
      resultsBox.innerHTML=`<div class="results-heading"><strong>${showing} available</strong><span>${blocked>0?`${blocked} age-restricted · `:''}}Closest first</span></div>`+
        data.places.map((place,i)=>`<article class="place-card${place.age_blocked?' age-blocked':''}" id="place-${i}" data-lat="${place.latitude}" data-lon="${place.longitude}">
          <div class="rank">${i+1}</div><div>
          <h2>${escapeHtml(place.name)}${place.age_label?`<span class="age-badge ${place.age_blocked?'blocked':'ok'}">${escapeHtml(place.age_label)}</span>`:''}</h2>
          <p>${escapeHtml(place.category)} · ${formatDistance(place.distance_m)}</p>
          ${place.address?`<small>${escapeHtml(place.address)}</small>`:''}
          ${place.age_blocked?`<small style="color:#b91c1c">⚠️ Age restriction — requires ${escapeHtml(place.age_label)}</small>`:''}
          </div></article>`).join('');
      document.querySelectorAll('.place-card:not(.age-blocked)').forEach(card=>{
        card.addEventListener('click',()=>{map.setView([Number(card.dataset.lat),Number(card.dataset.lon)],17)});
      });
    }
    form.addEventListener('submit',async event=>{
      event.preventDefault();
      statusBox.className='status loading';statusBox.textContent='Searching map data\u2026';
      resultsBox.innerHTML='';button.disabled=true;
      try{
        const response=await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({address:document.getElementById('address').value,
            age:parseInt(document.getElementById('age').value,10),
            radius:document.getElementById('radius').value,unit:unit.value,
            travelMode:document.getElementById('travelMode').value})});
        const ct=response.headers.get('content-type')||'';
        if(!ct.includes('application/json'))throw new Error(`Server error (HTTP ${response.status}): API returned non-JSON.`);
        const data=await response.json();
        if(!response.ok)throw new Error(data.error||`Search failed (HTTP ${response.status}).`);
        statusBox.className='status success';statusBox.textContent=`Searched around ${data.origin.name}`;
        renderResults(data);
      }catch(error){
        statusBox.className='status error';statusBox.textContent=error.message;
      }finally{button.disabled=false}
    });
  </script>
</body>
</html>"""


@dataclass
class Place:
    name: str
    latitude: float
    longitude: float
    distance_m: float
    category: str
    address: str = ""
    age_label: str = ""
    age_blocked: bool = False


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def radius_to_metres(value, unit, travel_mode):
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Radius must be greater than zero.")
    unit = unit.strip().lower()
    if unit == "km":
        metres = value * 1_000
    elif unit == "m":
        metres = value
    elif unit == "minutes":
        speeds = {"walk": 5, "bike": 15, "drive": 40}
        if travel_mode not in speeds:
            raise ValueError("Unsupported travel mode.")
        metres = value * speeds[travel_mode] * 1_000 / 60
    else:
        raise ValueError("Unsupported radius unit.")
    return min(metres, MAX_RADIUS_M)


def geocode(address):
    params = {"q": address, "format": "jsonv2", "limit": 1, "addressdetails": 1}
    if NOMINATIM_EMAIL:
        params["email"] = NOMINATIM_EMAIL
    r = requests.get(NOMINATIM_URL, params=params,
                     headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=15)
    r.raise_for_status()
    results = r.json()
    if not results:
        raise ValueError("The address could not be found.")
    res = results[0]
    try:
        lat, lon = float(res["lat"]), float(res["lon"])
    except (KeyError, TypeError, ValueError) as e:
        raise RuntimeError("Address service returned invalid coordinates.") from e
    return lat, lon, str(res.get("display_name") or address)


def build_overpass_query(lat, lon, radius_m):
    filters = [
        ("amenity", "restaurant|cafe|fast_food|food_court|bar|pub"),
        ("amenity", "coworking_space|library"),
        ("office", "coworking"),
    ]
    stmts = []
    for key, values in filters:
        for t in ("node", "way", "relation"):
            stmts.append(f'{t}["{key}"~"^({values})$"](around:{int(radius_m)},{lat},{lon});')
    return "[out:json][timeout:25];(" + "".join(stmts) + ");out center tags;"


def query_overpass(query):
    errors = []
    for endpoint in OVERPASS_URLS:
        try:
            r = requests.post(endpoint, data={"data": query},
                              headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=35)
            r.raise_for_status()
            data = r.json()
            elements = data.get("elements", [])
            if not isinstance(elements, list):
                raise RuntimeError("Map service returned invalid data.")
            return elements
        except (requests.RequestException, ValueError, RuntimeError) as e:
            errors.append(f"{endpoint}: {e}")
    raise RuntimeError("Map data service unavailable. " + " | ".join(errors))


def element_coordinates(el):
    if "lat" in el and "lon" in el:
        try:
            return float(el["lat"]), float(el["lon"])
        except (TypeError, ValueError):
            return None
    c = el.get("center")
    if isinstance(c, dict) and "lat" in c and "lon" in c:
        try:
            return float(c["lat"]), float(c["lon"])
        except (TypeError, ValueError):
            return None
    return None


def format_osm_address(tags):
    street = " ".join(x for x in (
        str(tags.get("addr:housenumber", "")).strip(),
        str(tags.get("addr:street", "")).strip()) if x)
    locality = str(tags.get("addr:city") or tags.get("addr:town") or tags.get("addr:village") or "").strip()
    postcode = str(tags.get("addr:postcode", "")).strip()
    return ", ".join(x for x in (street, locality, postcode) if x)


def apply_age_rules(category: str, user_age: int):
    """Return (age_label, age_blocked) for a category given user_age."""
    rule = CATEGORY_AGE_RULES.get(category)
    if not rule:
        return "", False
    min_age, max_age, label = rule
    if label is None:
        return "", False
    blocked = user_age < min_age or (max_age is not None and user_age > max_age)
    return label, blocked


def parse_places(elements, origin_lat, origin_lon, user_age):
    places, seen = [], set()
    for el in elements:
        if not isinstance(el, dict):
            continue
        coords = element_coordinates(el)
        if coords is None:
            continue
        lat, lon = coords
        tags = el.get("tags", {})
        if not isinstance(tags, dict):
            tags = {}
        amenity = str(tags.get("amenity", "")).lower()
        office = str(tags.get("office", "")).lower()
        if amenity in {"restaurant", "cafe", "fast_food", "food_court"}:
            category = "Restaurant / café"
        elif amenity in {"bar", "pub"}:
            category = "Bar / pub"
        elif amenity == "library":
            category = "Library"
        elif amenity == "coworking_space" or office == "coworking":
            category = "Coworking space"
        else:
            continue
        name = str(tags.get("name") or tags.get("brand") or f"Unnamed {category.lower()}").strip()
        dist = haversine_m(origin_lat, origin_lon, lat, lon)
        key = (name.casefold(), round(lat * 100_000), round(lon * 100_000))
        if key in seen:
            continue
        seen.add(key)
        age_label, age_blocked = apply_age_rules(category, user_age)
        places.append(Place(name=name, latitude=lat, longitude=lon,
                            distance_m=round(dist, 1), category=category,
                            address=format_osm_address(tags),
                            age_label=age_label, age_blocked=age_blocked))
    # Sort: available first, then blocked; within each group by distance
    places.sort(key=lambda p: (p.age_blocked, p.distance_m))
    return places[:MAX_RESULTS]


@app.get("/")
def index():
    return HTML, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "service": "radius-map"})


@app.post("/api/search")
def search_places():
    try:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ValueError("Send the request as JSON.")
        address = str(payload.get("address", "")).strip()
        age = int(payload.get("age", 0))
        radius_value = float(payload.get("radius", 0))
        radius_unit = str(payload.get("unit", "km")).strip().lower()
        travel_mode = str(payload.get("travelMode", "walk")).strip().lower()
        if not address:
            raise ValueError("Enter an address.")
        if age < 1 or age > 120:
            raise ValueError("Enter a valid age between 1 and 120.")
        radius_m = radius_to_metres(radius_value, radius_unit, travel_mode)
        lat, lon, display_name = geocode(address)
        elements = query_overpass(build_overpass_query(lat, lon, radius_m))
        places = parse_places(elements, lat, lon, age)
        return jsonify({
            "origin": {"latitude": lat, "longitude": lon, "name": display_name},
            "radius_m": round(radius_m),
            "places": [asdict(p) for p in places],
        })
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    except requests.Timeout:
        return jsonify({"error": "Address service timed out. Please try again."}), 504
    except requests.RequestException as e:
        app.logger.warning("Request failed: %s", e)
        return jsonify({"error": "Address service temporarily unavailable."}), 502
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except Exception:
        app.logger.exception("Search failed")
        return jsonify({"error": "An unexpected server error occurred."}), 500


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=int(os.environ.get("PORT", "5000")))
