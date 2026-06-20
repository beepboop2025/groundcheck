"""Evidence gathering. Default: Wikipedia (keyless). Override or disable via env."""
import re
from typing import List

import httpx

from . import config
from .models import Source

WIKI_API = "https://en.wikipedia.org/w/api.php"
_TAG = re.compile(r"<[^>]+>")
_ENTITIES = {"&quot;": '"', "&amp;": "&", "&#039;": "'", "&#39;": "'", "&apos;": "'", "&nbsp;": " "}


def _strip_html(s: str) -> str:
    s = _TAG.sub("", s)
    for ent, ch in _ENTITIES.items():
        s = s.replace(ent, ch)
    return s.strip()


class Retriever:
    def __init__(self) -> None:
        if config.SEARCH_BACKEND == "stub":
            self.backend = "stub"
        elif config.SEARCH_URL:
            self.backend = "custom"
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
        return await self._wikipedia(query, max_sources)

    async def _wikipedia(self, query: str, n: int) -> List[Source]:
        params = {
            "action": "query", "list": "search", "srsearch": query,
            "srlimit": str(n), "format": "json",
        }
        # Wikimedia's API enforces a descriptive User-Agent with a contact URL (generic
        # UAs get 403). https://meta.wikimedia.org/wiki/User-Agent_policy
        ua = "Groundcheck/0.2 (https://github.com/beepboop2025/groundcheck; MCP fact-check)"
        async with httpx.AsyncClient(timeout=15, headers={"user-agent": ua}) as c:
            r = await c.get(WIKI_API, params=params)
            r.raise_for_status()
            data = r.json()
        hits = (data.get("query", {}).get("search", []) or [])[:n]
        return [
            Source(
                title=h.get("title", ""),
                url=f"https://en.wikipedia.org/?curid={h.get('pageid')}",
                snippet=_strip_html(h.get("snippet", "")),
                stance=None,
            )
            for h in hits
        ]

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
