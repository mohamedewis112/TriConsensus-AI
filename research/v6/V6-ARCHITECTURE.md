# TriConsensus V6 — Adaptive MARL Architecture

## Safety invariants

- Paper/shadow only. Live-money execution remains prohibited.
- V5.1 statistical evidence and qualification gates remain scientifically unchanged.
- MARL cannot convert INSUFFICIENT_SAMPLE into qualified evidence.
- The portfolio adapter can only preserve or reduce the deterministic risk budget.
- A zero weight or blacklist resolves to NO_TRADE.
- The Dynamic Volatility Risk Agent can propose levels only inside bounded action ranges.
- The deterministic Risk Engine remains the final veto authority.
- Missing/stale IV, microstructure, policy, persistence, or model evidence fails closed when enforcement is enabled.
- Random neural weights are never used for a trading decision.

## LangGraph path

START
→ V5.1 evidence precheck
→ Meta-RL Portfolio Adapter
→ Dynamic Volatility Risk Agent
→ Market Intelligence Agent
→ Trade Strategist Agent
→ Risk & Devil's Advocate Agent
→ Full Consensus
→ Deterministic Risk Engine
→ Paper execution / NO_TRADE

V6 defaults to shadow mode. In shadow mode adaptive actions are audited but do not alter the V5.1 decision.

## State spaces

### Portfolio state (15 dimensions)

For BTC and ETH, using a rolling 20–40 reward-memory window:
- normalized sample count
- hit rate
- mean net return
- compounded net return
- max drawdown
- losing streak

Global:
- portfolio drawdown
- recent trade count
- warm-up-complete flag

### Dynamic risk state (8 dimensions)

- log(entry price)
- ATR / entry
- annualized realized volatility
- front-expiry option implied volatility
- Bybit order-book imbalance
- Binance order-book imbalance
- spread
- side encoding

## Action spaces

### Portfolio actor (4 bounded outputs)

- BTC weight in [0,1]
- ETH weight in [0,1]
- BTC blacklist probability
- ETH blacklist probability

Blacklisted assets are projected to weight 0. The deterministic Risk Engine multiplies its existing risk budget by the projected weight, so the adaptive layer cannot increase base risk.

### Dynamic risk actor (4 bounded outputs)

- stop ATR multiplier in [0.75, 2.25]
- reward/risk multiplier in [0.75, 1.50]
- trailing lock fraction in [0, 0.90]
- NO_TRADE probability / veto

The neuro-symbolic projection can further veto an action using stale evidence or adverse cross-venue order-book imbalance.

## Reward

reward = realized_net_return - lambda_dd * realized_max_drawdown

Default lambda_dd = 2.0.

Realized net return is paper PnL divided by entry notional.
Realized max drawdown is absolute MAE divided by entry notional.

## MAPPO implementation

- two decentralized lightweight MLP actors
- one centralized critic
- clipped PPO objective
- entropy regularization
- gradient clipping
- safe-prior bootstrap before any policy-generated exploration
- stochastic actions are permitted only in shadow mode
- enforcement inference is deterministic

The safe prior is deliberately conservative and non-random. PPO updates begin only after enough policy-generated terminal transitions contain both raw bounded actions and old behavior-policy log probabilities.

## Async learning path

Paper trade closes
→ immutable marl_reward_memory insert
→ AFTER INSERT trigger
→ PGMQ triconsensus_marl_rewards
→ V6PolicyUpdateWorker outside the 15m decision loop
→ warm-up / transition gate
→ safe-prior bootstrap or MAPPO update
→ append-only marl_policy_versions
→ next V6 cycle loads latest active policy

## Market evidence

- Bybit linear L50 order book: WebSocket stream, exchange millisecond timestamps.
- Binance USD-M partial depth: public WebSocket stream.
- Persistence worker throttles database writes while retaining the latest in-memory snapshot.
- Implied volatility comes from Bybit option ticker mark IV, weighted by open interest across nearest-expiry near-ATM contracts.
- Historical volatility is not mislabeled as implied volatility.

## Files

- backend/triconsensus/v6_models.py — Pydantic state/action/reward models
- backend/triconsensus/v6_reward.py — reward function and rolling state builder
- backend/triconsensus/v6_marl.py — MAPPO actors, critic, trainer, safe prior
- backend/triconsensus/v6_agents.py — constrained portfolio/risk agents
- backend/triconsensus/v6_microstructure.py — cross-venue stream parsers
- backend/triconsensus/v6_microstructure_worker.py — throttled persistence worker
- backend/triconsensus/v6_policy_worker.py — asynchronous PGMQ policy updater
- backend/triconsensus/graph.py — V6 LangGraph integration
- backend/triconsensus/cycle.py — V6 shadow context and audit wiring
- backend/triconsensus/paper_monitor.py — V6 trailing/horizon exits and terminal reward write
- backend/requirements-marl.txt — optional PyTorch/WebSocket runtime
- tests/test_v6_marl.py — action/risk invariants
- tests/test_v6_paper_monitor.py — trailing and 3h horizon invariants
- supabase/migrations/20261028143000_add_v6_adaptive_marl.sql — database migration
- research/v6/V6-SUPABASE-MARL-SCHEMA.json — machine-readable schema
