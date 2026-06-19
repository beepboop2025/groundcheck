import { test } from "node:test";
import assert from "node:assert/strict";
import { computeVerdict } from "../src/verdict.js";

const src = (stance, extra = {}) => ({ title: "", url: "", snippet: "", stance, ...extra });

test("no sources → unverified, zero confidence", () => {
  const v = computeVerdict("x", []);
  assert.equal(v.verdict, "unverified");
  assert.equal(v.confidence, 0);
});

test("stub-only sources are not evidence → unverified", () => {
  const v = computeVerdict("x", [src(null, { stub: true }), src(null, { stub: true })]);
  assert.equal(v.verdict, "unverified");
  assert.equal(v.confidence, 0);
});

test("evidence with no clear stance → unverified, low confidence", () => {
  const v = computeVerdict("x", [src("neutral"), src(null)]);
  assert.equal(v.verdict, "unverified");
  assert.ok(v.confidence <= 0.2);
});

test("two supports, no refutes → supported, confidence > single-source cap", () => {
  const v = computeVerdict("x", [src("supports"), src("supports")]);
  assert.equal(v.verdict, "supported");
  assert.ok(v.confidence > 0.6, `expected >0.6, got ${v.confidence}`);
});

test("lone supporting source is capped at 0.6", () => {
  const v = computeVerdict("x", [src("supports"), src("neutral")]);
  assert.equal(v.verdict, "supported");
  assert.ok(v.confidence <= 0.6, `expected ≤0.6, got ${v.confidence}`);
});

test("conflict (support + refute) → unverified, never majority-voted", () => {
  const v = computeVerdict("x", [src("supports"), src("supports"), src("refutes")]);
  assert.equal(v.verdict, "unverified");
  assert.match(v.rationale, /disagree/);
});

test("refutes only → refuted", () => {
  const v = computeVerdict("x", [src("refutes"), src("refutes")]);
  assert.equal(v.verdict, "refuted");
  assert.ok(v.confidence > 0.6);
});

test("confidence saturates (3 ≈ a bit more than 2, never 1)", () => {
  const two = computeVerdict("x", [src("supports"), src("supports")]).confidence;
  const three = computeVerdict("x", [src("supports"), src("supports"), src("supports")]).confidence;
  assert.ok(three > two && three < 1);
});
