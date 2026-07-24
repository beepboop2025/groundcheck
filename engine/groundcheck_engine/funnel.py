"""Payment-funnel observability — who reached the paywall, and where they stopped.

The x402 lane was, until this module, unmeasurable. Uvicorn's access log records
`POST /check -> 402` identically whether the caller was a census crawler that never
intended to pay, a buyer whose client could not parse our 402, or a funded buyer the
facilitator rejected. Those three have completely different fixes, and a service that
cannot tell them apart optimises blind.

So every request that touches a priced path is recorded as one funnel event with the
stage it reached:

    probe         a non-POST discovery hit (indexers GET priced paths to read the offer)
    unpaid        POST with no payment header at all
    free          served from the free daily quota
    malformed     a payment header arrived but did not decode
    verify_fail   the facilitator rejected the payment       <- a LOST SALE
    engine_error  we failed to produce a result, so we charged nothing
    settle_fail   result produced, settlement failed         <- a LOST SALE, and we ate the work
    paid          settled on-chain

The `unpaid` stage is the noisy one, so callers are classified by User-Agent. The
ecosystem runs a lot of unpaid traffic through paid endpoints on purpose: uptime
monitors, conformance graders, and at least six independent discovery indexes. Counting
those as lost demand would be self-deception, so they are bucketed separately and the
number that actually matters is `unknown` POSTs and any stage past `unpaid`.

Nothing here may break a request: every entry point swallows its own errors. Counters
live in memory (reset on restart); the JSONL file, when configured, is the durable copy.

Env:
  GROUNDCHECK_FUNNEL_LOG   path to an append-only JSONL file (unset = counters only)
  GROUNDCHECK_OPS_TOKEN    bearer token for GET /ops/funnel (unset = endpoint hidden)
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter, deque
from datetime import datetime, timezone
from typing import Deque, Optional

# Stages, in funnel order. Ordering is load-bearing: the ops summary reports them
# in this sequence so a drop-off is read top to bottom.
STAGES = ("probe", "unpaid", "free", "malformed", "verify_fail",
          "engine_error", "settle_fail", "paid")

# Stages that prove a buyer client got far enough to actually attempt payment.
# These are the only unpaid outcomes worth engineering against.
ATTEMPT_STAGES = ("malformed", "verify_fail", "settle_fail", "paid")

_MAX_RECENT = 200

# Known non-buyer traffic, by User-Agent substring (lowercased), observed in
# production. Keep this list evidence-based: add a pattern only after seeing the
# agent in the logs, because a wrong entry silently hides real demand.
_KNOWN_CALLERS = (
    # (substring, bucket)
    ("carbonmonitor", "monitor"),
    ("x402-observer", "monitor"),
    ("uptime", "monitor"),
    ("healthcheck", "monitor"),
    ("pingdom", "monitor"),
    ("betteruptime", "monitor"),
    ("coinbasebazaardiscovery", "indexer"),
    ("agentreeve", "indexer"),
    ("x402-census", "indexer"),
    ("x402register", "indexer"),
    ("x402stats", "indexer"),
    ("x402scan", "indexer"),
    ("x402-list", "indexer"),
    ("litebeam", "indexer"),
    ("bazaar", "indexer"),
    ("gptbot", "crawler"),
    ("claudebot", "crawler"),
    ("perplexitybot", "crawler"),
    ("oai-searchbot", "crawler"),
    ("bingbot", "crawler"),
    ("googlebot", "crawler"),
    ("semrush", "crawler"),
    ("ahrefs", "crawler"),
    # Hand-driven probes, including the operator's own. Kept out of `unknown`
    # because a real buyer must sign an EIP-3009 authorization, which in practice
    # means an SDK — nobody pays from a shell one-liner.
    ("curl/", "manual"),
    ("wget", "manual"),
    ("httpie", "manual"),
    ("insomnia", "manual"),
    ("postman", "manual"),
    ("censys", "scanner"),
    ("masscan", "scanner"),
    ("zgrab", "scanner"),
    ("nmap", "scanner"),
    ("expanse", "scanner"),
    ("paloalto", "scanner"),
    ("internet-measurement", "scanner"),
)

# User-Agents that a real x402 buyer plausibly ships with. Matching one does not
# prove intent to pay, but it does mean the caller is worth looking at by hand.
_BUYER_HINTS = ("x402", "axios", "node-fetch", "undici", "httpx", "requests",
                "python-httpx", "aiohttp", "agentkit", "mcp", "openai", "anthropic",
                "langchain", "llamaindex", "crewai")

_lock = threading.Lock()
_counts: Counter = Counter()
_by_caller: Counter = Counter()
_by_path: Counter = Counter()
_reasons: Counter = Counter()
_recent: Deque[dict] = deque(maxlen=_MAX_RECENT)
_started_at = time.time()


def classify_agent(ua: str) -> str:
    """Bucket a User-Agent: monitor | indexer | crawler | scanner | manual |
    buyer-like | unknown.

    Deliberately conservative. Anything unrecognised lands in `unknown`, which is the
    bucket the operator is supposed to read, so a new real buyer surfaces rather than
    being absorbed into a catch-all.
    """
    low = (ua or "").lower().strip()
    if not low:
        return "unknown"
    for needle, bucket in _KNOWN_CALLERS:
        if needle in low:
            return bucket
    if any(h in low for h in _BUYER_HINTS):
        return "buyer-like"
    return "unknown"


def payment_dialect(payment: Optional[dict]) -> str:
    """Which protocol generation the buyer spoke: v1, v2, or unparseable."""
    if not isinstance(payment, dict):
        return "unparsed"
    return "v2" if payment.get("x402Version") == 2 else "v1"


def _log_path() -> Optional[str]:
    """Where the durable copy goes, resolved fresh each call so an operator can turn
    the file log on or off without a code path caching the old answer.

    Falls back to systemd's $STATE_DIRECTORY because the deployment is a hardened
    unit (ProtectHome=read-only, ProtectSystem=strict) where almost nothing is
    writable. Pointing the log at a path the service cannot write is a silent no-op
    by design — record() must never raise — so the default has to be a path systemd
    itself guarantees, not one an operator has to guess.
    """
    raw = os.environ.get("GROUNDCHECK_FUNNEL_LOG", "").strip()
    if raw:
        return raw
    state = os.environ.get("STATE_DIRECTORY", "").strip()
    if state:
        # systemd passes a colon-separated list when several are configured.
        return os.path.join(state.split(":")[0], "funnel.jsonl")
    return None


def log_writable() -> tuple[bool, str]:
    """Can the durable log actually be written? For the operator summary only.

    record() deliberately swallows write failures, which means a misconfigured path
    looks exactly like an idle service. This is the loud channel that distinguishes
    them, and it is why /ops/funnel reports it rather than just the path.
    """
    dest = _log_path()
    if not dest:
        return False, "disabled (no GROUNDCHECK_FUNNEL_LOG or STATE_DIRECTORY)"
    try:
        with open(dest, "a", encoding="utf-8"):
            pass
        return True, dest
    except OSError as exc:
        return False, f"{dest}: {type(exc).__name__}: {exc}"


def record(stage: str,
           *,
           path: str,
           method: str = "POST",
           ip: str = "",
           ua: str = "",
           reason: str = "",
           dialect: str = "",
           amount_usd: Optional[float] = None,
           payer: str = "",
           tx: str = "") -> None:
    """Record one funnel event. Never raises: observability must not break payments."""
    try:
        caller = classify_agent(ua)
        event = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "stage": stage,
            "path": path,
            "method": method,
            "ip": ip,
            "ua": (ua or "")[:200],
            "caller": caller,
            "reason": (reason or "")[:300],
        }
        if dialect:
            event["dialect"] = dialect
        if amount_usd is not None:
            event["amount_usd"] = amount_usd
        if payer:
            event["payer"] = payer
        if tx:
            event["tx"] = tx

        with _lock:
            _counts[stage] += 1
            _by_caller[f"{caller}:{stage}"] += 1
            _by_path[f"{path}:{stage}"] += 1
            if reason:
                _reasons[f"{stage}:{(reason or '')[:120]}"] += 1
            # Keep the tail interesting: routine unpaid probes from known
            # infrastructure would otherwise evict every real signal.
            if stage != "probe" and not (stage == "unpaid" and caller in
                                         ("monitor", "indexer", "crawler",
                                          "scanner", "manual")):
                _recent.append(event)

        dest = _log_path()
        if dest:
            with open(dest, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, separators=(",", ":")) + "\n")
    except Exception:  # pragma: no cover - observability is strictly best-effort
        pass


def summary() -> dict:
    """Operator view of the funnel: stages, callers, drop-off reasons, recent events."""
    with _lock:
        counts = dict(_counts)
        callers = dict(_by_caller)
        paths = dict(_by_path)
        reasons = dict(_reasons)
        recent = list(_recent)

    attempts = sum(counts.get(s, 0) for s in ATTEMPT_STAGES)
    paid = counts.get("paid", 0)
    # Unpaid POSTs from callers we could not identify: the closest thing we have to
    # a "real buyer walked away" signal, and the number to watch after a compat fix.
    unknown_unpaid = sum(v for k, v in callers.items()
                         if k.endswith(":unpaid") and k.split(":", 1)[0] in
                         ("unknown", "buyer-like"))
    ok, where = log_writable()
    return {
        "since": datetime.fromtimestamp(_started_at, timezone.utc).isoformat(timespec="seconds"),
        "uptime_s": int(time.time() - _started_at),
        "stages": {s: counts.get(s, 0) for s in STAGES},
        "payment_attempts": attempts,
        "settled": paid,
        "conversion_of_attempts": round(paid / attempts, 4) if attempts else None,
        "unidentified_unpaid_posts": unknown_unpaid,
        "by_caller": callers,
        "by_path": paths,
        "drop_off_reasons": reasons,
        "recent": recent[-50:],
        "log_ok": ok,
        "log_file": where,
    }


def ops_token() -> str:
    return os.environ.get("GROUNDCHECK_OPS_TOKEN", "").strip()


def authorised(request_headers, query_token: str = "") -> bool:
    """Constant-ish-time check of the ops token from header or query string."""
    want = ops_token()
    if not want:
        return False
    got = (request_headers.get("X-Ops-Token")
           or request_headers.get("x-ops-token")
           or query_token
           or "")
    auth = request_headers.get("Authorization") or ""
    if not got and auth.lower().startswith("bearer "):
        got = auth[7:]
    if len(got) != len(want):
        return False
    return sum(a != b for a, b in zip(got, want)) == 0


def reset() -> None:
    """Test hook."""
    with _lock:
        _counts.clear()
        _by_caller.clear()
        _by_path.clear()
        _reasons.clear()
        _recent.clear()
