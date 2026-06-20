"""Environment-driven configuration for the engine."""
import os

# Retrieval backend: "stub" disables it; a custom URL overrides Wikipedia.
SEARCH_BACKEND = os.getenv("GROUNDCHECK_SEARCH_BACKEND")  # "stub" | None
SEARCH_URL = os.getenv("GROUNDCHECK_SEARCH_URL")          # custom JSON search endpoint
SEARCH_KEY = os.getenv("GROUNDCHECK_SEARCH_KEY")          # optional bearer token

# Where the canonical free-llm-router Python twin lives (added to sys.path if not installed).
ROUTER_PATH = os.getenv("GROUNDCHECK_ROUTER_PATH", "/Users/mrinal/free-llm-router/python")

# HTTP server bind.
HOST = os.getenv("GROUNDCHECK_ENGINE_HOST", "127.0.0.1")
PORT = int(os.getenv("GROUNDCHECK_ENGINE_PORT", "8723"))
