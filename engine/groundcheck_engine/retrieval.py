"""Evidence gathering. Default: Wikipedia + GDELT news, fanned out concurrently.

Wikipedia (keyless) supplies encyclopedic evidence as full intro paragraphs —
far stronger stance signal than search-snippet fragments. GDELT (keyless) adds
worldwide news coverage so recency-sensitive claims ("X announced Y") aren't
judged on an encyclopedia alone. Either backend failing is non-fatal: the fan-out
degrades to whichever side answered. Override with GROUNDCHECK_SEARCH_URL, or
disable everything with GROUNDCHECK_SEARCH_BACKEND=stub.
"""
import asyncio
import re
from typing import List

import httpx

from . import config
from .models import Source

WIKI_API = "https://en.wikipedia.org/w/api.php"
GDELT_API = "https://api.gdeltproject.org/api/v2/doc/doc"
# Wikimedia's API enforces a descriptive User-Agent with a contact URL (generic
# UAs get 403). https://meta.wikimedia.org/wiki/User-Agent_policy
_UA = "Groundcheck/0.3 (https://groundcheck.seiche.info; MCP fact-check)"
_SNIPPET_CHARS = 600

_TAG = re.compile(r"<[^>]+>")
_ENTITIES = {"&quot;": '"', "&amp;": "&", "&#039;": "'", "&#39;": "'", "&apos;": "'", "&nbsp;": " "}


def _strip_html(s: str) -> str:
    s = _TAG.sub("", s)
    for ent, ch in _ENTITIES.items():
        s = s.replace(ent, ch)
    return s.strip()


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text[:_SNIPPET_CHARS].rsplit(" ", 1)[0] if len(text) > _SNIPPET_CHARS else text


class Retriever:
    def __init__(self) -> None:
        if config.SEARCH_BACKEND == "stub":
            self.backend = "stub"
        elif config.SEARCH_URL:
            self.backend = "custom"
        elif config.NEWS_BACKEND:
            self.backend = "wikipedia+gdelt"
        else:
            self.backend = "wikipedia"

    @property
    def is_live(self) -> bool:
        return self.backend != "stub"

    async def search(self, query: str, max_sources: int = 5) -> List[Source]:
        if self.backend == "stub":
            return self._stub(query, max_sources)
        if self.backend == "custom":
            return await self._custom(query, max_sources)
        return await self._live(query, max_sources)

    async def _live(self, query: str, n: int) -> List[Source]:
        """Fan out to all live backends; merge with Wikipedia first, dedupe, cap."""
        tasks = [self._wikipedia(query, n)]
        if self.backend == "wikipedia+gdelt":
            tasks.append(self._gdelt(query, 3))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        wiki = results[0] if isinstance(results[0], list) else []
        news = results[1] if len(results) > 1 and isinstance(results[1], list) else []

        # News earns at most 2 of the slots; encyclopedic evidence fills the
        # rest, with surplus wiki hits as backfill if dedupe removes anything.
        take_news = news[:2]
        head = max(1, n - len(take_news))
        candidates = wiki[:head] + take_news + wiki[head:]
        seen, out = set(), []
        for s in candidates:
            key = s.url or s.title
            if key not in seen:
                seen.add(key)
                out.append(s)
        return out[:n]

    async def _wikipedia(self, query: str, n: int) -> List[Source]:
        # generator=search + prop=extracts returns the full intro paragraph per
        # hit in ONE request — a real evidence passage, not a snippet fragment.
        params = {
            "action": "query", "generator": "search", "gsrsearch": query,
            "gsrlimit": str(n), "prop": "extracts", "exintro": "1",
            "explaintext": "1", "format": "json",
        }
        async with httpx.AsyncClient(timeout=15, headers={"user-agent": _UA}) as c:
            r = await c.get(WIKI_API, params=params)
            r.raise_for_status()
            data = r.json()
        pages = (data.get("query", {}).get("pages", {}) or {}).values()
        hits = sorted(pages, key=lambda p: p.get("index", 99))[:n]
        return [
            Source(
                title=h.get("title", ""),
                url=f"https://en.wikipedia.org/?curid={h.get('pageid')}",
                snippet=_clip(_strip_html(h.get("extract") or h.get("title", ""))),
                stance=None,
            )
            for h in hits
        ]

    async def _gdelt(self, query: str, n: int) -> List[Source]:
        """Worldwide news coverage (GDELT DOC 2.0, keyless). Best-effort: any
        failure returns [] — news is a bonus signal, never a dependency."""
        params = {
            "query": query, "mode": "artlist", "maxrecords": str(n),
            "format": "json", "sort": "hybridrel",
        }
        try:
            async with httpx.AsyncClient(timeout=12, headers={"user-agent": _UA}) as c:
                r = await c.get(GDELT_API, params=params)
                r.raise_for_status()
                data = r.json()
        except Exception:  # noqa: BLE001 — malformed JSON, timeouts, 5xx: all soft
            return []
        out = []
        for a in (data.get("articles", []) or [])[:n]:
            title = _strip_html(a.get("title", ""))
            if not title:
                continue
            date = str(a.get("seendate", ""))[:8]
            out.append(Source(
                title=title,
                url=a.get("url", ""),
                snippet=f"News headline ({a.get('domain', 'unknown source')}, {date}): {title}",
                stance=None,
            ))
        return out

    async def _custom(self, query: str, n: int) -> List[Source]:
        headers = {"Authorization": f"Bearer {config.SEARCH_KEY}"} if config.SEARCH_KEY else {}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(config.SEARCH_URL, params={"q": query, "n": str(n)}, headers=headers)
            r.raise_for_status()
            data = r.json()
        return [
            Source(
                title=x.get("title", ""), url=x.get("url", ""),
                snippet=x.get("snippet", ""), stance=x.get("stance"),
            )
            for x in (data.get("results", []) or [])[:n]
        ]

    def _stub(self, query: str, n: int) -> List[Source]:
        # Honest placeholder. `stub=True` marks it as NOT evidence.
        return [
            Source(
                title=f"[stub source {i + 1}] search backend disabled",
                url="about:blank",
                snippet=f'Unset GROUNDCHECK_SEARCH_BACKEND=stub to actually verify: "{query}".',
                stance=None, stub=True,
            )
            for i in range(min(2, n))
        ]
