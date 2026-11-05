# TriConsensus AI V1 Architecture

## Safety invariant

Any uncertainty or subsystem failure resolves to **NO_TRADE**. V1 cannot submit live-money orders.

## Decision path

1. Bybit public market data
2. Feature engine
3. ML evidence layer
4. Market Intelligence Agent
5. Trade Strategist Agent
6. Risk & Devil's Advocate Agent
7. Full-consensus gate
8. Deterministic risk engine
9. Paper execution only
10. Immutable audit/event persistence
11. Analytics and Decision Replay

## Agent authority

Agents produce evidence and structured recommendations. They do not own risk limits, position sizing, or execution authority.

## V1 instruments

- BTC/USDT perpetual context
- ETH/USDT perpetual context
- Primary setup timeframe: 15m
- Context: 1m, 5m, 15m, 1h, 4h

## Failure policy

Market API failure, stale data, malformed model output, provider outage, agent disagreement, VETO, database failure, abnormal spread, or unavailable risk engine => NO_TRADE.
