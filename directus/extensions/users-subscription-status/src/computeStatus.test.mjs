// Plain Node test harness — no external deps. Run with `node --test`.
import test from "node:test";
import assert from "node:assert/strict";
import { computeStatus } from "./index.js";

const TODAY = new Date("2026-05-11T00:00:00Z");

test("blocked beats everything", () => {
  assert.equal(
    computeStatus({ is_blocked: true, is_trial: true, active_tariff_id: "10", is_promo_synthetic: false, expired_at: "2099-01-01" }, TODAY),
    "blocked"
  );
});

test("trial with no active tariff", () => {
  assert.equal(
    computeStatus({ is_blocked: false, is_trial: true, active_tariff_id: null, is_promo_synthetic: null, expired_at: null }, TODAY),
    "trial"
  );
});

test("no tariff, no trial -> none", () => {
  assert.equal(
    computeStatus({ is_blocked: false, is_trial: false, active_tariff_id: null, is_promo_synthetic: null, expired_at: null }, TODAY),
    "none"
  );
});

test("promo-synthetic regardless of expired_at", () => {
  assert.equal(
    computeStatus({ is_blocked: false, is_trial: false, active_tariff_id: "32030", is_promo_synthetic: true, expired_at: "2026-05-31" }, TODAY),
    "promo"
  );
});

test("paid_active when expired_at >= today", () => {
  assert.equal(
    computeStatus({ is_blocked: false, is_trial: false, active_tariff_id: "1", is_promo_synthetic: false, expired_at: "2026-05-11" }, TODAY),
    "paid_active"
  );
  assert.equal(
    computeStatus({ is_blocked: false, is_trial: false, active_tariff_id: "1", is_promo_synthetic: false, expired_at: "2026-06-21" }, TODAY),
    "paid_active"
  );
});

test("paid_expired when expired_at < today", () => {
  assert.equal(
    computeStatus({ is_blocked: false, is_trial: false, active_tariff_id: "1", is_promo_synthetic: false, expired_at: "2026-05-10" }, TODAY),
    "paid_expired"
  );
});

test("ISO datetime strings normalize to date", () => {
  assert.equal(
    computeStatus({ is_blocked: false, is_trial: false, active_tariff_id: "1", is_promo_synthetic: false, expired_at: "2026-05-11T23:00:00Z" }, TODAY),
    "paid_active"
  );
});

test("null row -> none", () => {
  assert.equal(computeStatus(null, TODAY), "none");
});
