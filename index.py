"""Vercel serverless entry point — imports the Flask app from the project root."""
import sys
from pathlib import Path

# Add project root to path so `app` module is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app  # noqa: F401  — Vercel looks for `app`
