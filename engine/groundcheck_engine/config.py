"""Environment-driven configuration for the engine."""
import os

# Retrieval backend: "stub" disables it; a custom URL overrides Wikipedia.
SEARCH_BACKEND = os.getenv("GROUNDCHECK_SEARCH_BACKEND")  # "stub" | None
SEARCH_URL = os.getenv("GROUNDCHECK_SEARCH_URL")          # custom JSON search endpoint
SEARCH_KEY = os.getenv("GROUNDCHECK_SEARCH_KEY")          # optional bearer token
NEWS_BACKEND = os.getenv("GROUNDCHECK_NEWS", "1") not in ("0", "off", "false")  # GDELT fan-out

# Verdict cache: repeat claims are served from memory (saves LLM quota, speeds
# up paid batch calls). Seconds; 0 disables. Best-effort per warm instance.
CACHE_TTL_S = int(os.getenv("GROUNDCHECK_CACHE_TTL_S", "21600"))

# Where the canonical free-llm-router Python twin lives (added to sys.path if not installed).
ROUTER_PATH = os.getenv("GROUNDCHECK_ROUTER_PATH", "/Users/mrinal/free-llm-router/python")

# HTTP server bind.
HOST = os.getenv("GROUNDCHECK_ENGINE_HOST", "127.0.0.1")
PORT = int(os.getenv("GROUNDCHECK_ENGINE_PORT", "8723"))

# ---- x402 pay-per-call (dormant unless GROUNDCHECK_X402_PAY_TO is set) ----
# Endpoint path -> USD per call. /verify and /search stay free by design:
# the single-claim surface is the adoption funnel; the batch document check
# is the one that burns real retrieval + LLM budget per claim.
X402_PRICES_USD = {
    "/check": float(os.getenv("GROUNDCHECK_X402_PRICE_CHECK", "0.02")),
}
# Free /check calls per IP per UTC day before a 402 is returned (0 = none).
# Best-effort per warm serverless instance, same caveat as the rate limiter.
X402_FREE_PER_DAY = int(os.getenv("GROUNDCHECK_X402_FREE_PER_DAY", "5"))
