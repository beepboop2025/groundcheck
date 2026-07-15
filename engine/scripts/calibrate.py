#!/usr/bin/env python3
"""Build the conformal calibration artifact for Groundcheck.

Runs the real pipeline (retrieval -> multi-model panel) over a labeled claim
set, fits per-provider ensemble weights, computes finite-sample conformal
thresholds per group, and writes calibration/calibration.json.

Usage (from engine/, with provider keys in the environment):
    python scripts/calibrate.py [--alpha 0.1] [--claims calibration/seed_claims.jsonl]

Notes on validity: the "<= alpha error" guarantee holds for claims exchangeable
with this calibration set. The seed set is short encyclopedic + ticker claims;
recalibrate with domain claims before selling the guarantee for a new domain.
Never reuse these claims as a test set — that is what "split" conformal means.
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vendor"))

from groundcheck_engine import conformal, ensemble  # noqa: E402
from groundcheck_engine.retrieval import Retriever  # noqa: E402

PACE_S = 5.0  # gap between claims: 3 providers/claim, sequential claims keeps
              # every free tier under its per-minute request and token caps


def load_claims(path: str) -> List[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            row.setdefault(
                "group", "instrument" if "$" in row["claim"] else "general")
            rows.append(row)
    return rows


async def score_claim(retriever: Retriever, row: dict) -> dict:
    sources = await retriever.search(row["claim"], 5)
    label, _, panelist_scores = await ensemble.classify_panel(row["claim"], sources)
    return {**row, "classifier": label, "panelist_scores": panelist_scores}


async def score_all(rows: List[dict], pace_s: float) -> List[dict]:
    """Sequential with pacing (rate limits, not wall-clock, are the constraint),
    plus one retry pass over claims that came back scoreless."""
    retriever = Retriever()
    print(f"calibrating on {len(rows)} claims via backend={retriever.backend}")
    scored: List[dict] = []
    for i, row in enumerate(rows):
        scored.append(await score_claim(retriever, row))
        n_ok = sum(1 for r in scored if r["panelist_scores"])
        if (i + 1) % 8 == 0:
            print(f"  {i + 1}/{len(rows)} claims, {n_ok} scored")
        await asyncio.sleep(pace_s)
    retry = [i for i, r in enumerate(scored) if not r["panelist_scores"]]
    if retry:
        print(f"retry pass over {len(retry)} scoreless claims")
        await asyncio.sleep(30)  # let per-minute windows reset
        for i in retry:
            scored[i] = await score_claim(retriever, scored[i])
            await asyncio.sleep(pace_s)
    return scored


def fit_weights(scored: List[dict]) -> Dict[str, float]:
    """Weight each panelist by skill above chance on the labeled set.

    skill = mean(p if true else 1-p) - 0.5, floored at a small epsilon so a
    bad-but-present panelist keeps a nonzero voice (its scores still carry
    information the conformal thresholds will absorb).
    """
    per: Dict[str, List[float]] = {}
    for row in scored:
        for name, p in row["panelist_scores"].items():
            per.setdefault(name, []).append(p if row["label"] else 1.0 - p)
    weights = {}
    for name, vals in per.items():
        skill = sum(vals) / len(vals) - 0.5
        weights[name] = max(skill, 0.02)
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alpha", type=float, default=0.1)
    ap.add_argument("--claims", default=os.path.join(
        os.path.dirname(__file__), "..", "calibration", "seed_claims.jsonl"))
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(__file__), "..", "calibration", "calibration.json"))
    args = ap.parse_args()

    rows = load_claims(args.claims)
    scored = [r for r in asyncio.run(score_all(rows, PACE_S))
              if r["panelist_scores"]]
    dropped = len(rows) - len(scored)
    if dropped:
        print(f"warning: {dropped} claims returned no panelist score (dropped)")
    if len(scored) < 20:
        print("error: too few scored claims to calibrate — check provider keys")
        return 1

    weights = fit_weights(scored)
    print("panelist weights:", json.dumps(weights, indent=2))

    scores_by_group: Dict[str, Dict[str, List[float]]] = {"global": {"true": [], "false": []}}
    for row in scored:
        s = ensemble.combine_scores(row["panelist_scores"], weights)
        bucket = "true" if row["label"] else "false"
        scores_by_group.setdefault(
            row["group"], {"true": [], "false": []})[bucket].append(s)
        scores_by_group["global"][bucket].append(s)

    artifact = conformal.build_artifact(
        alpha=args.alpha,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        scores_by_group=scores_by_group,
        weights=weights,
    )
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2)
        fh.write("\n")
    print(f"wrote {args.out}")

    for name, g in artifact["groups"].items():
        print(f"  group={name:<12} supported>{g['supported_threshold']} "
              f"(n_false={g['n_false']})  refuted<{g['refuted_threshold']} "
              f"(n_true={g['n_true']})")
    # Sanity: how many calibration claims would certify under their own group
    # (upper bound on live certification rate; NOT a validity check).
    would = 0
    for row in scored:
        s = ensemble.combine_scores(row["panelist_scores"], weights)
        g = artifact["groups"].get(row["group"]) or artifact["groups"]["global"]
        if row["label"] and g["supported_threshold"] is not None \
                and s > g["supported_threshold"]:
            would += 1
        if not row["label"] and g["refuted_threshold"] is not None \
                and s < g["refuted_threshold"]:
            would += 1
    print(f"in-sample certification rate: {would}/{len(scored)} "
          f"(directional claims clearing their threshold)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
