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

# ---- instrument resolution (OpenFIGI open symbology) ----
# Free API; a free key raises the rate limit 25/min -> 250/min.
OPENFIGI_URL = os.getenv("GROUNDCHECK_OPENFIGI_URL", "https://api.openfigi.com")
OPENFIGI_API_KEY = os.getenv("OPENFIGI_API_KEY")
# Instrument identity is stable — cache resolutions for a day by default.
RESOLVE_CACHE_TTL_S = int(os.getenv("GROUNDCHECK_RESOLVE_CACHE_TTL_S", "86400"))
# Entity-resolve explicit identifiers ($AAPL, ISINs, FIGIs) inside /verify.
VERIFY_RESOLVES_INSTRUMENTS = os.getenv(
    "GROUNDCHECK_VERIFY_INSTRUMENTS", "1") not in ("0", "off", "false")

# ---- atomic-claim decomposition (SURE-RAG / Fact in Fragments) ----
# Split a compound claim into independently-verified atoms and recombine
# weakest-link, so a true conjunct can't carry a false one to "supported".
DECOMPOSE = os.getenv("GROUNDCHECK_DECOMPOSE", "1") not in ("0", "off", "false")

# ---- calibrated verification (multi-model panel + conformal guarantee) ----
# The panel queries up to ENSEMBLE_MAX free providers concurrently; each claim
# costs that many LLM calls instead of one, paid for with a calibratable score.
ENSEMBLE = os.getenv("GROUNDCHECK_ENSEMBLE", "1") not in ("0", "off", "false")
ENSEMBLE_MAX = int(os.getenv("GROUNDCHECK_ENSEMBLE_MAX", "3"))
# Conformal calibration artifact (written by scripts/calibrate.py). Absent ->
# verdicts carry no guarantee and never claim one.
CALIBRATION_PATH = os.getenv(
    "GROUNDCHECK_CALIBRATION",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "calibration", "calibration.json"))

# ---- x402 pay-per-call (dormant unless GROUNDCHECK_X402_PAY_TO is set) ----
# Endpoint path -> USD per call. /verify and /search stay free by design:
# the single-claim surface is the adoption funnel; the batch document check
# is the one that burns real retrieval + LLM budget per claim. /resolve is the
# attested-enrichment call: canonical instrument identity with provenance.
X402_PRICES_USD = {
    "/check": float(os.getenv("GROUNDCHECK_X402_PRICE_CHECK", "0.02")),
    "/resolve": float(os.getenv("GROUNDCHECK_X402_PRICE_RESOLVE", "0.005")),
    # Granular verification-loop pricing (StableEnrich pattern): extract cheap,
    # ground per document, buy the full delivery attestation as the bundle.
    "/extract": float(os.getenv("GROUNDCHECK_X402_PRICE_EXTRACT", "0.005")),
    "/attest-delivery": float(
        os.getenv("GROUNDCHECK_X402_PRICE_ATTEST_DELIVERY", "0.05")),
}
# Free /check calls per IP per UTC day before a 402 is returned (0 = none).
# Best-effort per warm serverless instance, same caveat as the rate limiter.
X402_FREE_PER_DAY = int(os.getenv("GROUNDCHECK_X402_FREE_PER_DAY", "5"))
