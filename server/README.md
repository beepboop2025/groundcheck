# groundcheck-mcp

The grounding check agents run before they commit to an answer.

Groundcheck is an MCP server that verifies a factual claim against live
sources and returns a verdict (supported, refuted or unverified), a
confidence score, and cited sources. Call it mid task, before your agent
states a fact it is not sure of. It refuses to guess: conflicting evidence
returns unverified, and no evidence can never produce a supported verdict.

## Install

```bash
claude mcp add groundcheck -- npx -y groundcheck-mcp
```

Works the same way in Cursor, Cline, VS Code, or any MCP client that speaks
stdio.

## Tools

| Tool | What it does |
| --- | --- |
| `verify_claim` | Ground one factual claim. Returns verdict, confidence, sources. |
| `check_citations` | Extract and verify every claim in a draft. Per claim report. |
| `attribution_badge` | Markdown badge marking content as groundchecked. |

## Engine

This package is a thin stdio layer. The evidence engine (retrieval, stance
classification, verdict) is a Python service. By default the server looks
for it at `http://127.0.0.1:8723` and can spawn it from a local clone; set
`GROUNDCHECK_ENGINE_URL=https://groundcheck.seiche.info` to use the free
hosted engine instead.

Source, engine code, and self hosting guide:
https://github.com/beepboop2025/groundcheck

## License

MIT
