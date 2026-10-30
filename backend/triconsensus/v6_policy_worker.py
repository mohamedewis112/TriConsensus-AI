from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from typing import Any

import numpy as np

from .config import Settings
from .db import PersistenceFailure, SupabaseRepository
from .v6_agents import DynamicVolatilityRiskAgent, MetaRLPortfolioAdapterAgent
from .v6_marl import (
    MAPPOPolicyBundle,
    MAPPOTrainer,
    MARLDependencyError,
    PPOConfig,
    TORCH_AVAILABLE,
    torch,
)
from .v6_models import PortfolioState, RewardMemoryRecord, VolatilityRiskState
from .v6_reward import build_portfolio_state


class PolicyWorkerFailure(RuntimeError):
    pass


def _version(prefix: str) -> str:
    return prefix + "-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _hash_config(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _extract_transition(row: dict) -> dict[str, Any] | None:
    state = row.get("state_json") or {}
    action = row.get("action_json") or {}
    portfolio = action.get("portfolio") or {}
    dynamic_risk = action.get("dynamic_risk") or {}

    portfolio_state_raw = state.get("portfolio_state")
    risk_state_raw = state.get("volatility_state")
    portfolio_action = portfolio.get("policy_action")
    risk_action = dynamic_risk.get("policy_action")
    portfolio_log_prob = portfolio.get("policy_log_prob")
    risk_log_prob = dynamic_risk.get("policy_log_prob")

    if (
        portfolio_state_raw is None
        or risk_state_raw is None
        or portfolio_action is None
        or risk_action is None
        or portfolio_log_prob is None
        or risk_log_prob is None
    ):
        return None

    try:
        portfolio_state = PortfolioState.model_validate(portfolio_state_raw)
        risk_state = VolatilityRiskState.model_validate(risk_state_raw)
        portfolio_vec = MetaRLPortfolioAdapterAgent.state_vector(portfolio_state)
        risk_vec = DynamicVolatilityRiskAgent.state_vector(risk_state)
        portfolio_action = np.asarray(portfolio_action, dtype=float)
        risk_action = np.asarray(risk_action, dtype=float)
        if portfolio_action.shape != (4,) or risk_action.shape != (4,):
            return None
        if not np.isfinite(portfolio_action).all() or not np.isfinite(risk_action).all():
            return None
        reward = float(row["reward"])
    except (KeyError, TypeError, ValueError):
        return None

    return {
        "portfolio_state": portfolio_vec,
        "risk_state": risk_vec,
        "global_state": portfolio_vec + risk_vec,
        "portfolio_action": portfolio_action.tolist(),
        "risk_action": risk_action.tolist(),
        "portfolio_log_prob": float(portfolio_log_prob),
        "risk_log_prob": float(risk_log_prob),
        "reward": reward,
    }


class V6PolicyUpdateWorker:
    """Asynchronous PGMQ consumer for constrained MAPPO policy updates.

    This worker never runs inside the 15m decision path. Reward rows are immutable;
    queue messages are merely scheduling hints and can be archived after processing.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repo = SupabaseRepository(settings)
        self.ppo_config = PPOConfig()

    def _record_run(
        self,
        *,
        status: str,
        source_msg_id: int | None,
        source_reward_id: str | None,
        prior_policy_version: str | None,
        candidate_policy_version: str | None,
        sample_count: int,
        metrics: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        self.repo.insert(
            "marl_policy_update_runs",
            {
                "agent_role": "mappo_shared",
                "source_queue_msg_id": source_msg_id,
                "source_reward_id": source_reward_id,
                "prior_policy_version": prior_policy_version,
                "candidate_policy_version": candidate_policy_version,
                "sample_count": sample_count,
                "status": status,
                "metrics": metrics or {},
                "error_code": error_code,
            },
        )

    def _archive(self, messages: list[dict]) -> None:
        for message in messages:
            self.repo.archive_marl_reward(int(message["msg_id"]))

    def run_once(self, *, quantity: int = 32) -> dict[str, Any]:
        messages = self.repo.dequeue_marl_rewards(
            visibility_timeout=600,
            quantity=quantity,
        )
        if not messages:
            return {"status": "IDLE", "messages": 0, "trained": False}

        source_message = messages[-1]
        source_payload = source_message.get("message") or {}
        source_reward_id = source_payload.get("reward_id")
        rewards = self.repo.recent_marl_reward_memory(
            limit=self.settings.v6_reward_window * 2
        )

        records: list[RewardMemoryRecord] = []
        for row in rewards:
            try:
                records.append(
                    RewardMemoryRecord(
                        symbol=row["symbol"],
                        trade_id=str(row["trade_id"]),
                        policy_version=str(row["policy_version"]),
                        state=row.get("state_json") or {},
                        action=row.get("action_json") or {},
                        net_return=float(row["net_return"]),
                        max_drawdown=float(row["max_drawdown"]),
                        reward=float(row["reward"]),
                        terminal=bool(row.get("terminal", True)),
                        created_at=row.get("created_at"),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue

        warmup = build_portfolio_state(
            records,
            window=self.settings.v6_reward_window,
            minimum_warmup_trades_per_asset=(
                self.settings.v6_min_warmup_trades_per_asset
            ),
        )
        active = self.repo.latest_marl_policy("mappo_shared")
        prior_version = str(active["version"]) if active else None

        if not warmup.warmup_complete:
            self._record_run(
                status="skipped",
                source_msg_id=int(source_message["msg_id"]),
                source_reward_id=source_reward_id,
                prior_policy_version=prior_version,
                candidate_policy_version=None,
                sample_count=len(records),
                metrics={
                    "reason": "warmup_incomplete",
                    "BTC": warmup.assets["BTC/USDT"].sample_size,
                    "ETH": warmup.assets["ETH/USDT"].sample_size,
                },
            )
            self._archive(messages)
            return {
                "status": "SKIPPED",
                "reason": "warmup_incomplete",
                "messages": len(messages),
            }

        if not TORCH_AVAILABLE:
            self._record_run(
                status="failed",
                source_msg_id=int(source_message["msg_id"]),
                source_reward_id=source_reward_id,
                prior_policy_version=prior_version,
                candidate_policy_version=None,
                sample_count=len(records),
                error_code="TORCH_UNAVAILABLE",
            )
            raise PolicyWorkerFailure("install backend/requirements-marl.txt")

        bundle = MAPPOPolicyBundle(
            portfolio_input_dim=15,
            risk_input_dim=8,
            global_state_dim=23,
        )
        config_payload = {
            "ppo": self.ppo_config.__dict__,
            "reward_window": self.settings.v6_reward_window,
            "warmup_per_asset": self.settings.v6_min_warmup_trades_per_asset,
            "drawdown_penalty": self.settings.v6_drawdown_penalty,
            "risk_engine_authority": "deterministic_veto_supreme",
        }
        config_hash = _hash_config(config_payload)

        if active is None:
            candidate_version = _version("v6-safe-prior")
            adaptive_caps = self.repo.latest_v6_adaptive_safe_prior_caps()
            bundle.initialize_safe_prior(
                policy_version=candidate_version,
                portfolio_weight_caps=adaptive_caps,
            )
            self.repo.insert(
                "marl_policy_versions",
                {
                    "agent_role": "mappo_shared",
                    "version": candidate_version,
                    "status": "active",
                    "weights": bundle.export_payload(),
                    "training_sample_count": len(records),
                    "config_hash": config_hash,
                    "metrics": {
                        "bootstrap": "adaptive_v2_conservative_prior",
                        "adaptive_seed_caps": adaptive_caps,
                        "counterfactual_rewards_used": False,
                        "risk_actor_counterfactual_training": False,
                        "warmup_state": warmup.model_dump(mode="json"),
                    },
                },
            )
            self._record_run(
                status="trained",
                source_msg_id=int(source_message["msg_id"]),
                source_reward_id=source_reward_id,
                prior_policy_version=None,
                candidate_policy_version=candidate_version,
                sample_count=len(records),
                metrics={"bootstrap": True, "adaptive_seed_caps": adaptive_caps},
            )
            self._archive(messages)
            return {
                "status": "TRAINED",
                "mode": "SAFE_PRIOR_BOOTSTRAP",
                "policy_version": candidate_version,
                "messages": len(messages),
            }

        bundle.load_payload(active["weights"], policy_version=str(active["version"]))
        transitions = [
            transition
            for row in rewards
            if str(row.get("policy_version")) == str(active["version"])
            for transition in [_extract_transition(row)]
            if transition is not None
        ]

        minimum_ppo_transitions = max(20, self.settings.v6_min_warmup_trades_per_asset)
        if len(transitions) < minimum_ppo_transitions:
            self._record_run(
                status="skipped",
                source_msg_id=int(source_message["msg_id"]),
                source_reward_id=source_reward_id,
                prior_policy_version=str(active["version"]),
                candidate_policy_version=None,
                sample_count=len(transitions),
                metrics={"reason": "insufficient_behavior_policy_transitions"},
            )
            self._archive(messages)
            return {
                "status": "SKIPPED",
                "reason": "insufficient_behavior_policy_transitions",
                "sample_count": len(transitions),
                "messages": len(messages),
            }

        portfolio_states = torch.tensor(
            [row["portfolio_state"] for row in transitions],
            dtype=torch.float32,
        )
        risk_states = torch.tensor(
            [row["risk_state"] for row in transitions],
            dtype=torch.float32,
        )
        global_states = torch.tensor(
            [row["global_state"] for row in transitions],
            dtype=torch.float32,
        )
        portfolio_actions = torch.tensor(
            [row["portfolio_action"] for row in transitions],
            dtype=torch.float32,
        )
        risk_actions = torch.tensor(
            [row["risk_action"] for row in transitions],
            dtype=torch.float32,
        )
        portfolio_old_log_probs = torch.tensor(
            [row["portfolio_log_prob"] for row in transitions],
            dtype=torch.float32,
        )
        risk_old_log_probs = torch.tensor(
            [row["risk_log_prob"] for row in transitions],
            dtype=torch.float32,
        )
        returns = torch.tensor(
            [row["reward"] for row in transitions],
            dtype=torch.float32,
        )

        with torch.no_grad():
            values = bundle.critic(global_states)
            advantages = returns - values
            if len(transitions) > 1:
                advantages = (
                    advantages - advantages.mean()
                ) / advantages.std(unbiased=False).clamp_min(1e-6)

        trainer = MAPPOTrainer(bundle, self.ppo_config)
        metrics: dict[str, float] = {}
        for _ in range(4):
            metrics = trainer.update(
                portfolio_states=portfolio_states,
                portfolio_actions=portfolio_actions,
                portfolio_old_log_probs=portfolio_old_log_probs,
                risk_states=risk_states,
                risk_actions=risk_actions,
                risk_old_log_probs=risk_old_log_probs,
                global_states=global_states,
                returns=returns,
                advantages=advantages,
            )

        if not all(np.isfinite(float(value)) for value in metrics.values()):
            self._record_run(
                status="failed",
                source_msg_id=int(source_message["msg_id"]),
                source_reward_id=source_reward_id,
                prior_policy_version=str(active["version"]),
                candidate_policy_version=None,
                sample_count=len(transitions),
                metrics=metrics,
                error_code="NON_FINITE_MAPPO_UPDATE",
            )
            raise PolicyWorkerFailure("non-finite MAPPO update")

        candidate_version = _version("v6-mappo")
        bundle.policy_version = candidate_version
        self.repo.insert(
            "marl_policy_versions",
            {
                "agent_role": "mappo_shared",
                "version": candidate_version,
                "status": "active",
                "weights": bundle.export_payload(),
                "training_sample_count": len(transitions),
                "config_hash": config_hash,
                "metrics": metrics,
            },
        )
        self._record_run(
            status="trained",
            source_msg_id=int(source_message["msg_id"]),
            source_reward_id=source_reward_id,
            prior_policy_version=str(active["version"]),
            candidate_policy_version=candidate_version,
            sample_count=len(transitions),
            metrics=metrics,
        )
        self._archive(messages)
        return {
            "status": "TRAINED",
            "mode": "MAPPO_UPDATE",
            "policy_version": candidate_version,
            "sample_count": len(transitions),
            "metrics": metrics,
            "messages": len(messages),
        }


def main() -> None:
    settings = Settings()
    worker = V6PolicyUpdateWorker(settings)
    print(json.dumps(worker.run_once(), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
