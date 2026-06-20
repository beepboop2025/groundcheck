"""Vercel serverless entrypoint — exposes the FastAPI engine as `app`.

Vercel's Python runtime detects the module-level `app` (an ASGI application). The
engine package and the vendored free-llm-router live under engine/, which we add to
sys.path so they import the same way they do locally.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "engine", "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from groundcheck_engine.app import app  # noqa: E402

__all__ = ["app"]
