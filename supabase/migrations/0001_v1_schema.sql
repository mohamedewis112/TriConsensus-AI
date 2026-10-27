-- TriConsensus AI V1 baseline schema.
-- Source-of-truth companion to the already-provisioned Supabase project.
-- Backend-only access: anon/authenticated clients are intentionally denied.

create schema if not exists internal;

-- See project database for current schema. This file is intentionally introduced
-- as the baseline source-of-truth; future migrations must be additive and numbered.
-- Tables:
-- experiments, market_snapshots, feature_snapshots, ml_predictions, agent_runs,
-- consensus_decisions, risk_evaluations, paper_trades, trade_events, system_events.
--
-- Security invariants:
-- 1) RLS enabled on all public tables.
-- 2) anon/authenticated access explicitly denied.
-- 3) decision/audit snapshots are immutable.
-- 4) service_role is backend-only and must never be exposed to the browser.
