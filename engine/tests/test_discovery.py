"""Discovery-contract tests (x402scan / Bazaar indexers) — kept separate
from test_x402.py so the v2-native refactor can rewrite that file freely."""

import os

os.environ["GROUNDCHECK_SEARCH_BACKEND"] = "stub"

from fastapi.testclient import TestClient

from groundcheck_engine import app as app_mod

def test_openapi_schemas_are_inlined_not_refs():
    app_mod.app.openapi_schema = None
    schema = app_mod.app.openapi()
    body = schema["paths"]["/check"]["post"]["requestBody"]
    body_schema = body["content"]["application/json"]["schema"]
    assert "$ref" not in body_schema and body_schema.get("properties"), \
        "x402scan treats a bare $ref as a missing input schema"
    ok = schema["paths"]["/check"]["post"]["responses"]["200"]
    out_schema = ok["content"]["application/json"]["schema"]
    assert "$ref" not in out_schema and out_schema.get("properties")


def test_public_site_discovery_files_are_machine_readable():
    client = TestClient(app_mod.app)

    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    assert robots.headers["content-type"].startswith("text/plain")
    assert "Sitemap: https://groundcheck.seiche.info/sitemap.xml" in robots.text

    sitemap = client.get("/sitemap.xml")
    assert sitemap.status_code == 200
    assert sitemap.headers["content-type"].startswith("application/xml")
    assert "<loc>https://groundcheck.seiche.info/</loc>" in sitemap.text

    security = client.get("/.well-known/security.txt")
    assert security.status_code == 200
    assert "security/advisories/new" in security.text


def test_landing_identifies_the_canonical_product():
    landing = TestClient(app_mod.app).get("/")
    assert landing.status_code == 200
    assert '<link rel="canonical" href="https://groundcheck.seiche.info/"' in landing.text
    assert 'type="application/ld+json"' in landing.text
    assert "Live-source claim and citation verification" in landing.text
