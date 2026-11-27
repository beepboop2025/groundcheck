"""Where a language model sits in Groundcheck, stated once.

Exactly one step uses a language model: stance classification on retrieved
evidence (stance.py, ensemble.py). It runs on the grounding routes only:
/verify, /check, and the claim checks inside /attest-delivery. Claim
extraction (/extract) and instrument resolution (/resolve) are rule-based and
never call a model, and so are the schema check, the payment binding, the
hashes and the signature inside /attest-delivery.

Each result model carries the matching statement in `ai_disclosure`
(models.py), so the label travels with every answer: in the REST JSON and in
the MCP tool text alike, hosted or self-hosted. The tool descriptions in
mcp_http.py and in the npm server (server/src/server.ts) carry the short forms
below, and tests/test_ai_disclosure.py holds the README, the npm README, both
llms.txt copies, the landing page and the docs to the same facts.

Nothing here is signed: the attestation manifests (attest.py) bind their own
fixed key sets, so this field never changes a receipt.
"""

# Results of the grounding routes: /verify and /check, over REST and MCP.
GROUNDING = (
    "Opt-in AI service: a language model labels each retrieved passage as "
    "supporting, refuting or neutral on the claim, and a fixed rule turns those "
    "labels into the verdict and its confidence. Retrieval defaults to "
    "Wikipedia and GDELT news. Under that default, exact dated SOFR, EFFR and "
    "OBFR percentage claims are compared with the New York Fed's published rate "
    "instead, with no model. Any guarantee rests on the same models' scores, "
    "calibrated on general-knowledge and ticker-identity claims and not on "
    "finance figures, so nothing here is a finance guarantee. The classifier "
    "field names what ran for this result."
)

# Results of /attest-delivery. Its claim checks reuse the /check pipeline.
DELIVERY = (
    "Partly AI: the schema conformance check, the payment receipt binding, the "
    "hashes and the signature are rule-based and use no model. The claim checks "
    "under grounding run the same language model step as /check, so those "
    "verdicts, and a delivery_verdict that rests on them, are model-assisted. "
    "Calibrated on general-knowledge and ticker-identity claims, not finance "
    "figures: no claim verdict here is a finance guarantee."
)

# Results of /extract.
EXTRACT = (
    "No language model: claims are pulled out by sentence rules and "
    "conjunction splitting, so the same text always yields the same claims."
)

# Results of /resolve.
RESOLVE = (
    "No language model: identifiers are matched by rule against OpenFIGI open "
    "symbology records."
)

# Short forms for advertised metadata. Scanners grade that text alone, so
# these name no tool, carry no URL and give the model no instruction
# (tests/test_metadata_hygiene.py), and they keep every description under
# 1,000 characters. Both MCP surfaces carry them verbatim.
TOOL_GROUNDING = "Opt-in AI service: a language model labels each source's stance."
TOOL_DELIVERY = ("The claim checks use a language model for stance; the other "
                 "checks are rule-based.")
TOOL_RULE_BASED = "Rule-based, no language model."
TOOL_FINANCE = "The calibration set holds no finance figures."
# The finance limit as it reads inside a longer sentence.
FINANCE_PHRASE = "no finance figures"
# The server instructions describe the workflow by role, never by tool name.
SERVER_NOTE = ("Grounding is an opt-in AI step (a language model labels each "
               "source's stance); splitting and resolving use no model.")
