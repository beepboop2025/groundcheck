"""Discovery-contract tests (x402scan / Bazaar indexers) — kept separate
from test_x402.py so the v2-native refactor can rewrite that file freely."""

import os

os.environ["GROUNDCHECK_SEARCH_BACKEND"] = "stub"

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
