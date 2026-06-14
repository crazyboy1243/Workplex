from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass, field

import requests
from flask import Flask, jsonify, request
from groq import Groq

app = Flask(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_EMAIL = os.environ.get("NOMINATIM_EMAIL", "").strip()
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
USER_AGENT = os.environ.get(
    "RADIUS_MAP_USER_AGENT",
    "RadiusMapStudentProject/2.0 (contact: 787005@pdsb.net)",
).strip()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

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
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Radius Map</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    /* ── Reset & tokens ─────────────────────────── */
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --indigo:   #4f46e5;
      --indigo-d: #4338ca;
      --indigo-l: #eef2ff;
      --indigo-m: #a5b4fc;
      --ink:      #0f172a;
      --muted:    #64748b;
      --border:   #e2e8f0;
      --surface:  #ffffff;
      --bg:       #f8fafc;
      --green:    #16a34a;
      --green-l:  #dcfce7;
      --red:      #b91c1c;
      --red-l:    #fee2e2;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif;
      color: var(--ink);
    }
    html, body { height: 100%; overflow: hidden; background: var(--bg); }
    #map { position: fixed; inset: 0; z-index: 0; }

    /* ── Side panel (desktop) ──────────────────── */
    .panel {
      position: fixed; top: 0; left: 0; bottom: 0; width: 380px;
      background: var(--surface); z-index: 400;
      overflow-y: auto; box-shadow: 4px 0 32px rgba(15,23,42,.12);
      display: flex; flex-direction: column; overscroll-behavior: contain;
    }
    .panel-inner { padding: 28px 24px 40px; }

    /* ── Bottom sheet (mobile) ─────────────────── */
    @media (max-width: 700px) {
      .panel {
        top: auto; left: 0; right: 0; bottom: 0; width: 100%;
        max-height: 88vh; border-radius: 20px 20px 0 0;
        box-shadow: 0 -8px 40px rgba(15,23,42,.16);
        transform: translateY(0);
        transition: transform .3s cubic-bezier(.32,.72,0,1);
      }
      .panel.collapsed { transform: translateY(calc(100% - 88px)); }
      .panel-inner { padding: 4px 18px 48px; }
      .drag-handle {
        display: flex; justify-content: center; padding: 12px 0 6px; cursor: grab; flex-shrink: 0;
      }
      .drag-handle::after {
        content: ''; width: 36px; height: 4px; background: var(--border); border-radius: 99px;
      }
    }
    @media (min-width: 701px) { .drag-handle { display: none; } }
    .panel-scroll { flex: 1; overflow-y: auto; overscroll-behavior: contain; }

    /* ── Header ────────────────────────────────── */
    .eyebrow { font-size:.68rem; font-weight:800; letter-spacing:.14em; color:var(--indigo); text-transform:uppercase; margin-bottom:6px; }
    h1 { font-size:1.9rem; font-weight:900; letter-spacing:-.04em; line-height:1.1; }
    .subtitle { color:var(--muted); font-size:.87rem; line-height:1.5; margin-top:5px; margin-bottom:0; }

    /* ── Form ──────────────────────────────────── */
    label.field-label { display:block; font-size:.8rem; font-weight:700; color:var(--ink); margin:14px 0 5px; }
    input[type="text"], input[type="number"], select {
      width:100%; padding:11px 13px; border:1.5px solid var(--border); border-radius:10px;
      font:inherit; font-size:.9rem; background:var(--surface); color:var(--ink);
      transition:border-color .15s,box-shadow .15s; -webkit-appearance:none;
    }
    input:focus, select:focus { outline:none; border-color:var(--indigo); box-shadow:0 0 0 3px rgba(79,70,229,.12); }
    .row2 { display:grid; grid-template-columns:1fr 1fr; gap:10px; }

    /* chips */
    .chips { display:grid; grid-template-columns:1fr 1fr; gap:7px; margin-top:5px; }
    .chip-label {
      display:flex; align-items:center; gap:6px;
      border:1.5px solid var(--border); border-radius:9px; padding:9px 10px;
      cursor:pointer; font-size:.82rem; font-weight:500;
      transition:background .12s,border-color .12s;
    }
    .chip-label:has(input:checked) { background:var(--indigo-l); border-color:var(--indigo-m); }
    .chip-label input { display:none; }

    /* smart search toggle */
    .smart-row {
      display:flex; align-items:center; gap:9px; margin-top:14px;
      padding:11px 13px; border:1.5px solid var(--border); border-radius:10px;
      cursor:pointer; transition:border-color .12s,background .12s;
    }
    .smart-row:has(input:checked) { border-color:var(--indigo-m); background:var(--indigo-l); }
    .smart-row input { display:none; }
    .smart-title { font-size:.86rem; font-weight:700; }
    .smart-sub { font-size:.75rem; color:var(--muted); }
    .toggle-pill {
      width:36px; height:20px; border-radius:99px; background:var(--border);
      position:relative; transition:background .15s; flex-shrink:0;
    }
    .toggle-pill::after {
      content:''; position:absolute; top:2px; left:2px;
      width:16px; height:16px; border-radius:50%;
      background:white; box-shadow:0 1px 4px rgba(0,0,0,.2); transition:transform .15s;
    }
    .smart-row:has(input:checked) .toggle-pill { background:var(--indigo); }
    .smart-row:has(input:checked) .toggle-pill::after { transform:translateX(16px); }

    /* button */
    #searchButton {
      margin-top:16px; width:100%; border:0; border-radius:11px; padding:14px;
      font:inherit; font-size:.95rem; font-weight:800; color:white; background:var(--indigo);
      cursor:pointer; box-shadow:0 6px 16px rgba(79,70,229,.28);
      transition:background .12s,transform .1s; -webkit-appearance:none;
    }
    #searchButton:hover { background:var(--indigo-d); }
    #searchButton:active { transform:scale(.98); }
    #searchButton:disabled { opacity:.5; cursor:wait; transform:none; }

    /* status */
    #status { margin-top:14px; border-radius:10px; font-size:.84rem; line-height:1.45; }
    #status.loading { padding:10px 12px; background:var(--indigo-l); color:#3730a3; }
    #status.success { padding:10px 12px; background:var(--green-l); color:var(--green); }
    #status.error   { padding:10px 12px; background:var(--red-l);   color:var(--red); }
    #travelModeNote small { color:var(--muted); font-size:.77rem; }

    /* results */
    .results { margin-top:16px; }
    .results-heading { display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }
    .results-heading strong { font-size:.93rem; }
    .results-heading span { font-size:.78rem; color:var(--muted); }
    .place-card {
      display:grid; grid-template-columns:32px 1fr; gap:10px;
      border:1.5px solid var(--border); border-radius:12px; padding:12px; margin-bottom:8px;
      cursor:pointer; transition:box-shadow .15s,transform .15s,border-color .15s;
    }
    .place-card:hover:not(.age-blocked) { box-shadow:0 4px 24px rgba(15,23,42,.10); transform:translateY(-1px); border-color:var(--indigo-m); }
    .place-card.age-blocked { opacity:.42; border-color:#fecaca; background:#fff5f5; cursor:not-allowed; }
    .rank {
      width:30px; height:30px; border-radius:8px; background:var(--indigo-l); color:var(--indigo-d);
      font-weight:800; font-size:.83rem; display:grid; place-items:center;
    }
    .age-blocked .rank { background:var(--red-l); color:var(--red); }
    .place-card h2 { font-size:.9rem; font-weight:700; margin-bottom:3px; line-height:1.3; }
    .place-card p  { color:var(--muted); font-size:.78rem; margin-bottom:3px; }
    .place-card small { color:var(--muted); font-size:.73rem; line-height:1.4; }
    .badge {
      display:inline-block; font-size:.67rem; font-weight:800;
      padding:1px 6px; border-radius:99px; margin-left:5px; vertical-align:middle;
    }
    .badge-green { background:var(--green-l); color:var(--green); }
    .badge-red   { background:var(--red-l);   color:var(--red); }
    .badge-gray  { background:#f1f5f9; color:#94a3b8; }
    .badge-pulse { background:#f1f5f9; color:#64748b; animation:pulse 1.2s infinite; }
    @keyframes pulse { 0%,100%{opacity:1}50%{opacity:.4} }

    /* FAB */
    #mapFab {
      display:none; position:fixed; bottom:calc(88px + 16px); right:16px; z-index:500;
      width:48px; height:48px; border-radius:50%; background:var(--surface); color:var(--ink);
      border:none; box-shadow:0 4px 24px rgba(15,23,42,.10); font-size:1.3rem;
      cursor:pointer; align-items:center; justify-content:center; transition:box-shadow .15s;
    }
    @media (max-width:700px) { #mapFab { display:flex; } }

    /* emoji markers */
    .emoji-marker span {
      display:grid; place-items:center; width:32px; height:32px; border-radius:50%;
      background:white; box-shadow:0 3px 10px rgba(0,0,0,.22); font-size:16px;
    }
    .panel-scroll::-webkit-scrollbar { width:5px; }
    .panel-scroll::-webkit-scrollbar-thumb { background:var(--border); border-radius:99px; }
    @media (prefers-reduced-motion:reduce) { *,*::before,*::after { animation-duration:0s!important; transition-duration:0s!important; } }

    /* job filter chips — 3-col for wider options */
    .chips-3 { display:grid; grid-template-columns:1fr 1fr 1fr; gap:7px; margin-top:5px; }
    .filter-section { margin-top:16px; }
    .filter-hint { font-size:.73rem; color:var(--muted); margin-top:4px; }

    /* address row with location button */
    .address-row { display:flex; gap:7px; align-items:flex-end; }
    .address-row input { flex:1; }
    #locBtn {
      flex-shrink:0; height:42px; width:42px; border:1.5px solid var(--border); border-radius:10px;
      background:var(--surface); cursor:pointer; font-size:1.1rem;
      display:grid; place-items:center; transition:border-color .15s,background .15s;
      -webkit-appearance:none;
    }
    #locBtn:hover { border-color:var(--indigo); background:var(--indigo-l); }
    #locBtn:disabled { opacity:.5; cursor:wait; }
    #locBtn.locating { animation:pulse 1s infinite; }

    /* source links on cards */
    .source-links { display:flex; flex-wrap:wrap; gap:6px; margin-top:7px; }
    .source-link {
      font-size:.72rem; font-weight:600; color:var(--indigo);
      border:1.5px solid var(--indigo-m); border-radius:6px; padding:3px 8px;
      text-decoration:none; background:var(--indigo-l);
      transition:background .12s,border-color .12s; white-space:nowrap;
    }
    .source-link:hover { background:#e0e7ff; border-color:var(--indigo); }

    /* ── Address autocomplete ──────────────────── */
    .addr-wrap { position:relative; flex:1; }
    #suggestions {
      position:absolute; top:calc(100% + 4px); left:0; right:0; z-index:9999;
      background:var(--surface); border:1.5px solid var(--border); border-radius:10px;
      box-shadow:0 8px 32px rgba(15,23,42,.13); overflow:hidden; display:none;
    }
    .sugg-item {
      padding:10px 13px; cursor:pointer; font-size:.85rem; line-height:1.35;
      border-bottom:1px solid var(--border); display:flex; align-items:center; gap:9px;
      transition:background .1s;
    }
    .sugg-item:last-child { border-bottom:none; }
    .sugg-item:hover, .sugg-item.active { background:var(--indigo-l); }
    .sugg-icon { font-size:1rem; flex-shrink:0; width:20px; text-align:center; }
    .sugg-main { font-weight:600; color:var(--ink); }
    .sugg-sub { font-size:.74rem; color:var(--muted); margin-top:1px; }
  </style>
</head>
<body>
  <section id="map" aria-label="Interactive map"></section>

  <aside class="panel collapsed" id="panel" aria-label="Search panel">
    <div class="drag-handle" id="dragHandle" role="button" aria-label="Toggle panel" tabindex="0"></div>
    <div class="panel-scroll">
      <div class="panel-inner">
        <header style="margin-bottom:20px">
          <p class="eyebrow">OpenStreetMap · AI Hiring</p>
          <h1>Radius Map</h1>
          <p class="subtitle">Find nearby spots and see who's hiring.</p>
        </header>

        <form id="searchForm">
          <label class="field-label" for="address">Starting address</label>
          <div class="address-row">
            <div class="addr-wrap">
              <input id="address" name="address" type="text"
                     placeholder="Country, city, street…" required autocomplete="off" role="combobox" aria-autocomplete="list" aria-controls="suggestions" aria-expanded="false">
              <div id="suggestions" role="listbox" aria-label="Address suggestions"></div>
            </div>
            <button type="button" id="locBtn" title="Use current location" aria-label="Use current location">📍</button>
          </div>

          <label class="field-label" for="businessName">Business name <span style="font-weight:400;color:var(--muted)">(optional)</span></label>
          <input id="businessName" name="businessName" type="text" placeholder="e.g. Tim Hortons, City Hall…" autocomplete="off">

          <div class="row2">
            <div>
              <label class="field-label" for="age">Age <span style="font-weight:400;color:var(--muted)">(recommended)</span></label>
              <input id="age" name="age" type="number" min="1" max="120" placeholder="e.g. 17" inputmode="numeric">
            </div>
            <div>
              <label class="field-label" for="radius">Radius <span style="font-weight:400;color:var(--muted)">(recommended)</span></label>
              <input id="radius" name="radius" type="number" min="0.1" step="0.1" value="2" inputmode="decimal">
            </div>
          </div>

          <div class="row2">
            <div>
              <label class="field-label" for="unit">Unit <span style="font-weight:400;color:var(--muted)">(recommended)</span></label>
              <select id="unit" name="unit">
                <option value="" selected>Not selected</option>
                <option value="km">Kilometres</option>
                <option value="m">Metres</option>
                <option value="minutes">Minutes</option>
              </select>
            </div>
            <div>
              <label class="field-label" for="travelMode">Travel <span style="font-weight:400;color:var(--muted)">(recommended)</span></label>
              <select id="travelMode" name="travelMode">
                <option value="" selected>Not selected</option>
                <option value="walk">🚶 Walk</option>
                <option value="bike">🚲 Cycle</option>
                <option value="drive">🚗 Drive</option>
              </select>
            </div>
          </div>
          <div id="travelModeNote" hidden style="margin-top:5px">
            <small>Minutes use estimated speed, not live routing.</small>
          </div>

          <label class="smart-row">
            <span style="font-size:1.1rem">✨</span>
            <span style="flex:1">
              <span class="smart-title">Smart Search</span><br>
              <span class="smart-sub">AI job listing detection</span>
            </span>
            <input type="checkbox" id="hiringOnly">
            <span class="toggle-pill"></span>
          </label>

          <!-- ── Job Filters ─────────────────────────── -->
          <div class="filter-section">
            <label class="field-label">Pay Grade <span style="font-weight:400;color:var(--muted)">(optional, 1–30)</span></label>
            <div class="row2">
              <div>
                <input id="payGradeMin" name="payGradeMin" type="number" min="1" max="30" placeholder="Min (e.g. 4)" inputmode="numeric">
              </div>
              <div>
                <input id="payGradeMax" name="payGradeMax" type="number" min="1" max="30" placeholder="Max (e.g. 12)" inputmode="numeric">
              </div>
            </div>
          </div>

          <div class="filter-section">
            <label class="field-label">Shift Type <span style="font-weight:400;color:var(--muted)">(optional)</span></label>
            <div class="chips" id="shiftTypeFilters">
              <label class="chip-label"><input type="checkbox" value="full-time">⏰ Full-Time</label>
              <label class="chip-label"><input type="checkbox" value="part-time">🕐 Part-Time</label>
              <label class="chip-label"><input type="checkbox" value="morning">🌅 Morning</label>
              <label class="chip-label"><input type="checkbox" value="evening">🌆 Evening</label>
              <label class="chip-label"><input type="checkbox" value="night">🌙 Night</label>
              <label class="chip-label"><input type="checkbox" value="weekends">📅 Weekends</label>
            </div>
            <p class="filter-hint">Unselected = any shift</p>
          </div>

          <div class="filter-section">
            <label class="field-label">Job Type <span style="font-weight:400;color:var(--muted)">(optional)</span></label>
            <div class="chips" id="jobTypeFilters">
              <label class="chip-label"><input type="checkbox" value="permanent">🏢 Permanent</label>
              <label class="chip-label"><input type="checkbox" value="contract">📋 Contract</label>
              <label class="chip-label"><input type="checkbox" value="seasonal">🍂 Seasonal</label>
              <label class="chip-label"><input type="checkbox" value="casual">🎯 Casual</label>
            </div>
            <p class="filter-hint">Unselected = any type</p>
          </div>

          <div class="filter-section">
            <label class="field-label">Job Categories <span style="font-weight:400;color:var(--muted)">(optional)</span></label>
            <div class="chips chips-3" id="jobCategoryFilters">
              <label class="chip-label"><input type="checkbox" value="education">🎓 Education</label>
              <label class="chip-label"><input type="checkbox" value="programs">📌 Programs</label>
              <label class="chip-label"><input type="checkbox" value="volunteer">🤝 Volunteer</label>
              <label class="chip-label"><input type="checkbox" value="co-op">🔬 Co-op</label>
              <label class="chip-label"><input type="checkbox" value="trades">🔧 Trades</label>
              <label class="chip-label"><input type="checkbox" value="healthcare">🏥 Healthcare</label>
              <label class="chip-label"><input type="checkbox" value="technology">💻 Technology</label>
              <label class="chip-label"><input type="checkbox" value="retail">🛍️ Retail</label>
              <label class="chip-label"><input type="checkbox" value="food-service">🍳 Food Service</label>
            </div>
            <p class="filter-hint">Unselected = all categories</p>
          </div>

          <button type="submit" id="searchButton">Search nearby</button>
        </form>

        <div id="status" class="status" aria-live="polite"></div>
        <section id="results" class="results"></section>
      </div>
    </div>
  </aside>

  <button id="mapFab" aria-label="Search" title="Search">🔍</button>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
  (function(){
    const panel=document.getElementById('panel');
    const handle=document.getElementById('dragHandle');
    const fab=document.getElementById('mapFab');
    const isMobile=()=>window.innerWidth<=700;

    function expandPanel(){ panel.classList.remove('collapsed'); fab.textContent='🗺️'; fab.setAttribute('aria-label','View map'); }
    function collapsePanel(){ panel.classList.add('collapsed'); fab.textContent='🔍'; fab.setAttribute('aria-label','Search'); }

    handle.addEventListener('click',()=>{ if(!isMobile())return; panel.classList.contains('collapsed')?expandPanel():collapsePanel(); });
    handle.addEventListener('keydown',e=>{ if(e.key==='Enter'||e.key===' '){e.preventDefault();handle.click();} });
    fab.addEventListener('click',()=>{ panel.classList.contains('collapsed')?expandPanel():collapsePanel(); });

    let dragStart=null;
    handle.addEventListener('touchstart',e=>{dragStart=e.touches[0].clientY;},{passive:true});
    handle.addEventListener('touchend',e=>{
      if(dragStart==null)return;
      const dy=e.changedTouches[0].clientY-dragStart; dragStart=null;
      if(dy>40)collapsePanel(); else if(dy<-40)expandPanel();
    },{passive:true});

    window.addEventListener('resize',()=>{ if(!isMobile())panel.classList.remove('collapsed'); });
    if(!isMobile())panel.classList.remove('collapsed');

    const map=L.map('map').setView([43.6532,-79.3832],12);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; <a href="https://openstreetmap.org">OpenStreetMap</a>'}).addTo(map);
    let resultLayer=L.layerGroup().addTo(map),radiusCircle=null;

    const unit=document.getElementById('unit');
    const travelNote=document.getElementById('travelModeNote');
    const statusBox=document.getElementById('status');
    const resultsBox=document.getElementById('results');
    const button=document.getElementById('searchButton');

    unit.addEventListener('change',()=>{travelNote.hidden=unit.value!=='minutes';});

    // ── Use current location ──────────────────────
    const locBtn=document.getElementById('locBtn');
    const addrInput=document.getElementById('address');
    locBtn.addEventListener('click',()=>{
      if(!navigator.geolocation){alert('Geolocation is not supported by your browser.');return;}
      locBtn.disabled=true;locBtn.classList.add('locating');locBtn.textContent='⏳';
      navigator.geolocation.getCurrentPosition(async pos=>{
        const {latitude:lat,longitude:lon}=pos.coords;
        try{
          const r=await fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${lat}&lon=${lon}`,{headers:{'User-Agent':'WorkplexHiringApp/1.0'}});
          const d=await r.json();
          addrInput.value=d.display_name||`${lat},${lon}`;
        }catch{addrInput.value=`${lat.toFixed(6)},${lon.toFixed(6)}`;}
        locBtn.disabled=false;locBtn.classList.remove('locating');locBtn.textContent='📍';
      },err=>{
        locBtn.disabled=false;locBtn.classList.remove('locating');locBtn.textContent='📍';
        const msgs={1:'Location access denied.',2:'Location unavailable.',3:'Location request timed out.'};
        alert(msgs[err.code]||'Could not get location.');
      },{timeout:10000,enableHighAccuracy:true});
    });

    // ── Address autocomplete ──────────────────────
    const suggBox=document.getElementById('suggestions');
    let suggData=[],activeIdx=-1,debTimer=null;

    // Map OSM types to icons and readable labels
    function placeIcon(type,cls){
      const icons={country:'🌍',state:'🗺️',province:'🗺️',region:'🗺️',county:'🏞️',
        city:'🏙️',town:'🏘️',village:'🏡',hamlet:'🏡',suburb:'🏘️',neighbourhood:'🏘️',quarter:'🏘️',
        road:'🛣️',street:'🛣️',path:'🛤️',pedestrian:'🛤️',
        house:'🏠',building:'🏢',postcode:'📮',
        airport:'✈️',station:'🚉',bus_stop:'🚌'};
      return icons[type]||icons[cls]||'📌';
    }
    function placeType(type,cls,addresstype){
      const labels={country:'Country',state:'State / Province',county:'County',
        city:'City',town:'Town',village:'Village',hamlet:'Hamlet',suburb:'Suburb',
        neighbourhood:'Neighbourhood',road:'Street',house:'Address',building:'Building',
        postcode:'Postcode',airport:'Airport',station:'Station'};
      return labels[type]||labels[addresstype]||labels[cls]||(type?type.replace(/_/g,' '):'Place');
    }

    function showSugg(){
      if(!suggData.length){suggBox.style.display='none';addrInput.setAttribute('aria-expanded','false');return;}
      suggBox.innerHTML=suggData.map((s,i)=>`
        <div class="sugg-item${i===activeIdx?' active':''}" role="option" data-i="${i}">
          <span class="sugg-icon">${placeIcon(s.type,s.class)}</span>
          <div><div class="sugg-main">${esc(s.display_name.split(',')[0])}</div>
          <div class="sugg-sub">${esc(s.display_name.split(',').slice(1,4).join(',').trim())} <span style="color:var(--indigo-d);font-weight:600">${placeType(s.type,s.class,s.addresstype)}</span></div></div>
        </div>`).join('');
      suggBox.style.display='block';addrInput.setAttribute('aria-expanded','true');
      suggBox.querySelectorAll('.sugg-item').forEach(el=>{
        el.addEventListener('mousedown',e=>{e.preventDefault();selectSugg(parseInt(el.dataset.i));});
      });
    }

    function selectSugg(i){
      if(i<0||i>=suggData.length)return;
      addrInput.value=suggData[i].display_name;
      suggBox.style.display='none';addrInput.setAttribute('aria-expanded','false');
      suggData=[];activeIdx=-1;
    }

    async function fetchSugg(q){
      if(q.length<2){suggData=[];showSugg();return;}
      try{
        // Accept all OSM feature classes; featuretype param broadens results
        const url=`https://nominatim.openstreetmap.org/search?format=jsonv2&q=${encodeURIComponent(q)}&limit=7&addressdetails=0&dedupe=1`;
        const r=await fetch(url,{headers:{'Accept-Language':'en','User-Agent':'WorkplexHiringApp/1.0'}});
        suggData=await r.json();
        activeIdx=-1;showSugg();
      }catch{suggData=[];showSugg();}
    }

    addrInput.addEventListener('input',()=>{
      clearTimeout(debTimer);
      debTimer=setTimeout(()=>fetchSugg(addrInput.value.trim()),280);
    });

    addrInput.addEventListener('keydown',e=>{
      if(!suggData.length)return;
      if(e.key==='ArrowDown'){e.preventDefault();activeIdx=Math.min(activeIdx+1,suggData.length-1);showSugg();}
      else if(e.key==='ArrowUp'){e.preventDefault();activeIdx=Math.max(activeIdx-1,-1);showSugg();}
      else if(e.key==='Enter'&&activeIdx>=0){e.preventDefault();selectSugg(activeIdx);}
      else if(e.key==='Escape'){suggBox.style.display='none';addrInput.setAttribute('aria-expanded','false');}
    });

    document.addEventListener('click',e=>{
      if(!suggBox.contains(e.target)&&e.target!==addrInput){suggBox.style.display='none';addrInput.setAttribute('aria-expanded','false');}
    });
    function esc(v){return String(v).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'})[c]);}
    function fmtDist(m){return m<1000?`${Math.round(m)} m`:`${(m/1000).toFixed(2)} km`;}
    function markerIcon(cat,blocked){
      const e=cat==='Restaurant / café'?'🍽️':cat==='Library'?'📚':cat==='Bar / pub'?'🍺':'💻';
      return L.divIcon({className:'emoji-marker',html:`<span style="background:${blocked?'#fee2e2':'white'}">${e}</span>`,iconSize:[32,32],iconAnchor:[16,16]});
    }

    function applyHiringFilter(){
      const hiringOnly=document.getElementById('hiringOnly').checked;
      document.querySelectorAll('.place-card').forEach(card=>{
        if(!hiringOnly){card.style.display='';return;}
        const badge=card.querySelector('.hiring-badge');
        const isHiring=badge&&badge.dataset.hiring==='true';
        const isLoading=badge&&badge.classList.contains('badge-pulse');
        card.style.display=(isHiring||isLoading)?'':'none';
      });
    }

    async function loadHiringStatus(place,i,address,filters){
      const badge=document.getElementById(`hiring-${i}`);
      if(!badge)return;
      try{
        const payload={name:place.name,address};
        if(filters){if(filters.payGrade)payload.payGrade=filters.payGrade;if(filters.payGradeMin)payload.payGradeMin=filters.payGradeMin;if(filters.payGradeMax)payload.payGradeMax=filters.payGradeMax;if(filters.shiftTypes&&filters.shiftTypes.length)payload.shiftTypes=filters.shiftTypes;if(filters.jobTypes&&filters.jobTypes.length)payload.jobTypes=filters.jobTypes;if(filters.jobCategories&&filters.jobCategories.length)payload.jobCategories=filters.jobCategories;}
        const res=await fetch('/api/hiring',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        const d=await res.json();
        badge.className='badge';
        if(d.hiring){badge.dataset.hiring='true';badge.textContent='✅ Hiring';badge.classList.add('badge-green');if(d.url){badge.style.cursor='pointer';badge.title='View listings';badge.onclick=()=>window.open(d.url,'_blank');}}
        else{badge.dataset.hiring='false';badge.textContent='Not hiring';badge.classList.add('badge-gray');}
      }catch(e){badge.className='badge badge-gray';badge.textContent='?';}
      applyHiringFilter();
    }

    function renderResults(data){
      resultLayer.clearLayers();
      if(radiusCircle)map.removeLayer(radiusCircle);
      const origin=[data.origin.latitude,data.origin.longitude];
      L.marker(origin).addTo(resultLayer).bindPopup(`<strong>Start</strong><br>${esc(data.origin.name)}`);
      radiusCircle=L.circle(origin,{radius:data.radius_m,weight:2,fillOpacity:.07,color:'#4f46e5'}).addTo(map);
      data.places.forEach((place,i)=>{
        L.marker([place.latitude,place.longitude],{icon:markerIcon(place.category,place.age_blocked)})
          .addTo(resultLayer).bindPopup(`<strong>${esc(place.name)}</strong><br>${esc(place.category)}<br>${fmtDist(place.distance_m)}`)
          .on('click',()=>document.getElementById(`place-${i}`)?.scrollIntoView({behavior:'smooth',block:'center'}));
      });
      map.fitBounds(radiusCircle.getBounds(),{padding:[25,25]});
      if(!data.places.length){resultsBox.innerHTML='<p style="color:var(--muted);font-size:.87rem;padding:8px 0">No places found in this radius.</p>';return;}
      const checkHiring=document.getElementById('hiringOnly').checked;
      const showing=data.places.filter(p=>!p.age_blocked).length;
      const blocked=data.places.length-showing;
      resultsBox.innerHTML=`<div class="results-heading"><strong>${showing} available</strong><span>${blocked>0?blocked+' age-restricted · ':''}Closest first</span></div>`+
        data.places.map((place,i)=>{
          const q=encodeURIComponent(place.name);
          const loc=encodeURIComponent(place.address||data.origin.name.split(',').slice(0,2).join(','));
          const indeedUrl=`https://ca.indeed.com/jobs?q=${q}&l=${loc}`;
          const linkedInUrl=`https://www.linkedin.com/jobs/search/?keywords=${q}&location=${loc}`;
          const companyUrl=`https://www.google.com/search?q=${q}+careers+jobs+site`;
          return `<article class="place-card${place.age_blocked?' age-blocked':''}" id="place-${i}" data-lat="${place.latitude}" data-lon="${place.longitude}">
          <div class="rank">${i+1}</div>
          <div>
            <h2>${esc(place.name)}${place.age_label?`<span class="badge ${place.age_blocked?'badge-red':'badge-green'}">${esc(place.age_label)}</span>`:''}${checkHiring?`<span class="badge badge-pulse hiring-badge" id="hiring-${i}" data-hiring="">Checking\u2026</span>`:''}</h2>
            <p>${esc(place.category)} \u00b7 ${fmtDist(place.distance_m)}</p>
            <p style="font-size:.8rem;color:var(--muted);margin-top:2px">📍 ${esc(place.address||'Address unavailable')}</p>
            ${place.age_blocked?`<small style="color:var(--red)">\u26a0\ufe0f Requires ${esc(place.age_label)}</small>`:''}
            <div class="source-links">
              <a class="source-link" href="${indeedUrl}" target="_blank" rel="noopener">Indeed</a>
              <a class="source-link" href="${linkedInUrl}" target="_blank" rel="noopener">LinkedIn</a>
              <a class="source-link" href="${companyUrl}" target="_blank" rel="noopener">Company Site</a>
            </div>
          </div>
        </article>`;}).join('');
      document.querySelectorAll('.place-card:not(.age-blocked)').forEach(card=>{
        card.addEventListener('click',e=>{
          if(e.target.classList.contains('hiring-badge'))return;
          if(e.target.classList.contains('source-link'))return;
          map.setView([Number(card.dataset.lat),Number(card.dataset.lon)],17);
          if(isMobile())collapsePanel();
        });
      });
      if(checkHiring){
        const cityHint=data.origin.name.split(',').slice(0,2).join(',');
        data.places.forEach((place,i)=>{if(!place.age_blocked)loadHiringStatus(place,i,place.address||cityHint,data.filters||null);});
      }
    }

    document.getElementById('searchForm').addEventListener('submit',async event=>{
      event.preventDefault();
      const payGrade=document.getElementById('payGrade')?.value.trim()||null;
      const payGradeMin=document.getElementById('payGradeMin').value.trim()||null;
      const payGradeMax=document.getElementById('payGradeMax').value.trim()||null;
      const businessName=document.getElementById('businessName').value.trim()||null;
      const shiftTypes=[...document.querySelectorAll('#shiftTypeFilters input:checked')].map(c=>c.value);
      const jobTypes=[...document.querySelectorAll('#jobTypeFilters input:checked')].map(c=>c.value);
      const jobCategories=[...document.querySelectorAll('#jobCategoryFilters input:checked')].map(c=>c.value);
      const ageVal=document.getElementById('age').value.trim();
      const radiusVal=document.getElementById('radius').value.trim();
      statusBox.className='status loading';statusBox.textContent='Searching map data\u2026';
      resultsBox.innerHTML='';button.disabled=true;
      try{
        const response=await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({address:document.getElementById('address').value,
            age:ageVal?parseInt(ageVal,10):null,
            radius:radiusVal?parseFloat(radiusVal):2,unit:unit.value||'km',
            travelMode:document.getElementById('travelMode').value||'walk',
            payGrade:payGradeMin||payGradeMax?null:null,
            payGradeMin:payGradeMin?parseInt(payGradeMin,10):null,
            payGradeMax:payGradeMax?parseInt(payGradeMax,10):null,
            businessName,
            shiftTypes:shiftTypes.length?shiftTypes:null,
            jobTypes:jobTypes.length?jobTypes:null,
            jobCategories:jobCategories.length?jobCategories:null})});
        const ct=response.headers.get('content-type')||'';
        if(!ct.includes('application/json'))throw new Error(`Server error (HTTP ${response.status})`);
        const data=await response.json();
        if(!response.ok)throw new Error(data.error||`Search failed (HTTP ${response.status})`);
        statusBox.className='status success';statusBox.textContent=`Found near ${data.origin.name}`;
        renderResults(data);
        if(isMobile())expandPanel();
      }catch(error){statusBox.className='status error';statusBox.textContent=error.message;}
      finally{button.disabled=false;}
    });
  })();
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
    hiring: bool | None = None
    hiring_url: str = ""


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
    return "[out:json][timeout:12];(" + "".join(stmts) + ");out center tags;"


def query_overpass(query):
    errors = []
    for endpoint in OVERPASS_URLS:
        try:
            r = requests.post(endpoint, data={"data": query},
                              headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=15)
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


def parse_places(elements, origin_lat, origin_lon, user_age, categories):
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
        if category not in categories:
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
        age_raw = payload.get("age")
        age = int(age_raw) if age_raw is not None else 0
        radius_value = float(payload.get("radius") or 2)
        radius_unit = str(payload.get("unit", "km")).strip().lower()
        travel_mode = str(payload.get("travelMode", "walk")).strip().lower()
        categories = payload.get("categories")
        pay_grade = payload.get("payGrade")
        pay_grade_min = payload.get("payGradeMin")
        pay_grade_max = payload.get("payGradeMax")
        shift_types = payload.get("shiftTypes")
        job_types = payload.get("jobTypes")
        job_categories = payload.get("jobCategories")
        business_name = str(payload.get("businessName") or "").strip().lower()
        if not isinstance(categories, list) or not categories:
            categories = list(CATEGORY_AGE_RULES.keys())
        if not address:
            raise ValueError("Enter an address.")
        if age_raw is not None and (age < 1 or age > 120):
            raise ValueError("Age must be between 1 and 120.")
        radius_m = radius_to_metres(radius_value, radius_unit, travel_mode)
        lat, lon, display_name = geocode(address)
        elements = query_overpass(build_overpass_query(lat, lon, radius_m))
        places = parse_places(elements, lat, lon, age, categories)
        if business_name:
            places = [p for p in places if business_name in p.name.lower()]
        return jsonify({
            "origin": {"latitude": lat, "longitude": lon, "name": display_name},
            "radius_m": round(radius_m),
            "places": [asdict(p) for p in places],
            "filters": {
                "payGrade": int(pay_grade) if pay_grade is not None else None,
                "payGradeMin": int(pay_grade_min) if pay_grade_min is not None else None,
                "payGradeMax": int(pay_grade_max) if pay_grade_max is not None else None,
                "shiftTypes": shift_types or [],
                "jobTypes": job_types or [],
                "jobCategories": job_categories or [],
            },
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


def groq_search_hiring(name: str, address: str, pay_grade=None, pay_grade_min=None, pay_grade_max=None, shift_types=None, job_types=None, job_categories=None) -> tuple[bool, str]:
    """Use Groq compound-beta with web_search to check if a place is hiring.
    Returns (is_hiring, listing_url)."""
    if not GROQ_API_KEY:
        return False, ""
    try:
        client = Groq(api_key=GROQ_API_KEY)
        filter_hints = []
        if pay_grade_min or pay_grade_max:
            grade_range = f"{pay_grade_min or '?'}–{pay_grade_max or '?'}"
            filter_hints.append(f"pay grade {grade_range}")
        elif pay_grade:
            filter_hints.append(f"pay grade {pay_grade}")
        if shift_types:
            filter_hints.append(f"shift: {', '.join(shift_types)}")
        if job_types:
            filter_hints.append(f"job type: {', '.join(job_types)}")
        if job_categories:
            filter_hints.append(f"category: {', '.join(job_categories)}")
        filter_str = f" Prefer listings matching: {'; '.join(filter_hints)}." if filter_hints else ""
        prompt = (
            f'Search for current job listings for "{name}" located at "{address}".{filter_str} '
            f'Look on Indeed, LinkedIn, Glassdoor, or their own website. '
            f'Reply in JSON only, no markdown, with keys: '
            f'"hiring" (true/false) and "url" (the best job listing URL, or empty string). '
            f'Example: {{"hiring": true, "url": "https://ca.indeed.com/..."}} '
            f'If you find any active listings, hiring=true. If none found, hiring=false.'
        )
        completion = client.chat.completions.create(
            model="compound-beta",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_completion_tokens=256,
            top_p=1,
            stream=False,
            stop=None,
        )
        raw = completion.choices[0].message.content or ""
        # Strip markdown fences if present
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        import json
        data = json.loads(raw)
        return bool(data.get("hiring")), str(data.get("url") or "")
    except Exception as e:
        app.logger.warning("Groq hiring check failed for %s: %s", name, e)
        return False, ""


@app.post("/api/hiring")
def check_hiring():
    """Check hiring status for a single place."""
    try:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ValueError("Send JSON with name and address.")
        name = str(payload.get("name", "")).strip()
        address = str(payload.get("address", "")).strip()
        pay_grade = payload.get("payGrade")
        pay_grade_min = payload.get("payGradeMin")
        pay_grade_max = payload.get("payGradeMax")
        shift_types = payload.get("shiftTypes")
        job_types = payload.get("jobTypes")
        job_categories = payload.get("jobCategories")
        if not name:
            raise ValueError("name is required.")
        if not GROQ_API_KEY:
            return jsonify({"error": "GROQ_API_KEY not configured."}), 503
        hiring, url = groq_search_hiring(name, address, pay_grade=pay_grade, pay_grade_min=pay_grade_min, pay_grade_max=pay_grade_max, shift_types=shift_types, job_types=job_types, job_categories=job_categories)
        return jsonify({"hiring": hiring, "url": url})
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        app.logger.exception("Hiring check failed")
        return jsonify({"error": "Hiring check failed."}), 500
