# Radius Map — Flask + HTML + Vercel

A Flask web application that accepts an address and a radius, then finds nearby restaurants, cafés, libraries, and coworking spaces using OpenStreetMap data.

## Project structure

```text
radius_map_web/
├── app.py
├── requirements.txt
├── vercel.json
├── .python-version
├── .env.example
├── .gitignore
├── templates/
│   └── index.html
└── static/
    └── style.css
```

## Run locally

```bash
python -m venv .venv
```

Activate the virtual environment:

```bash
# Windows
.venv\Scripts\activate

# macOS or Linux
source .venv/bin/activate
```

Install dependencies and start the server:

```bash
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`.

For local environment variables, copy `.env.example` to `.env` and load those values in your terminal or IDE. Do not commit `.env`.

## Deploy through GitHub and Vercel

1. Create a new GitHub repository.
2. Upload all files and folders from this project. Do not upload only the ZIP file.
3. In Vercel, select **Add New → Project** and import the GitHub repository.
4. Leave the Framework Preset as **Other** if Vercel does not automatically detect Flask.
5. In **Project Settings → Environment Variables**, add:

   - `RADIUS_MAP_USER_AGENT` — for example, `RadiusMap/1.0 (contact: your-email@example.com)`
   - `NOMINATIM_EMAIL` — your contact email for Nominatim requests

6. Click **Deploy**.

Vercel detects the exported Flask variable named `app` in `app.py`. The HTML template and static CSS are served by Flask.

## Endpoints

- `GET /` — web interface
- `POST /api/search` — search API
- `GET /api/health` — health check

## Notes

- A minutes-based radius is an estimate based on walking, cycling, or driving speed. It is not live route-time navigation.
- Public Nominatim and Overpass servers may rate-limit heavy traffic. For a high-traffic production application, use hosted or paid geocoding and places services.
- Never commit passwords, private API keys, or `.env` files to GitHub.
