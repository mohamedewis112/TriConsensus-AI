from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

try:
    import torch
    from torch import nn
    from torch.distributions import Normal
except Exception:  # pragma: no cover - optional heavy dependency
    torch = None
    nn = None
    Normal = None


class MARLDependencyError(RuntimeError):
    pass


class MARLPolicyNotReady(RuntimeError):
    pass


TORCH_AVAILABLE = torch is not None and nn is not None and Normal is not None


@dataclass(frozen=True)
class PPOConfig:
    clip_ratio: float = 0.20
    actor_lr: float = 3e-4
    critic_lr: float = 1e-3
    entropy_coef: float = 0.01
    value_coef: float = 0.5
    max_grad_norm: float = 0.5


if TORCH_AVAILABLE:

    class _Actor(nn.Module):
        def __init__(self, input_dim: int, action_dim: int, hidden: int = 32) -> None:
            super().__init__()
            self.body = nn.Sequential(
                nn.Linear(input_dim, hidden),
                nn.Tanh(),
                nn.Linear(hidden, hidden),
                nn.Tanh(),
            )
            self.mean = nn.Linear(hidden, action_dim)
            self.log_std = nn.Parameter(torch.full((action_dim,), -1.5))

        def distribution(self, x: "torch.Tensor") -> "Normal":
            h = self.body(x)
            mean = self.mean(h)
            std = self.log_std.clamp(-4.0, 1.0).exp().expand_as(mean)
            return Normal(mean, std)

        def deterministic(self, x: "torch.Tensor") -> "torch.Tensor":
            return torch.sigmoid(self.distribution(x).mean)


    class _CentralizedCritic(nn.Module):
        def __init__(self, input_dim: int, hidden: int = 64) -> None:
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(input_dim, hidden),
                nn.Tanh(),
                nn.Linear(hidden, hidden),
                nn.Tanh(),
                nn.Linear(hidden, 1),
            )

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":
            return self.net(x).squeeze(-1)


    class MAPPOPolicyBundle:
        """Two decentralized actors with one centralized critic.

        Production inference is disabled until an explicit trained state payload is loaded.
        Randomly initialized weights are never used for a trading decision.
        """

        def __init__(
            self,
            *,
            portfolio_input_dim: int,
            risk_input_dim: int,
            global_state_dim: int,
        ) -> None:
            self.portfolio_actor = _Actor(portfolio_input_dim, 4)
            self.risk_actor = _Actor(risk_input_dim, 4)
            self.critic = _CentralizedCritic(global_state_dim)
            self.ready = False
            self.policy_version = "untrained"

        @staticmethod
        def _tensor(values: list[float] | np.ndarray) -> "torch.Tensor":
            return torch.as_tensor(values, dtype=torch.float32).reshape(1, -1)

        @staticmethod
        def _logit(value: float) -> float:
            clipped = min(1.0 - 1e-5, max(1e-5, value))
            return float(np.log(clipped / (1.0 - clipped)))

        def initialize_safe_prior(
            self,
            *,
            policy_version: str = "v6-safe-prior-v0",
            portfolio_weight_caps: dict[str, float] | None = None,
        ) -> None:
            """Initialize a conservative non-random policy for shadow exploration.

            Optional learned per-asset caps may only reduce the default 0.95
            portfolio weights. They never increase the base risk envelope.
            """
            caps = portfolio_weight_caps or {}
            btc_cap = min(0.95, max(0.0, float(caps.get("BTC/USDT", 0.95))))
            eth_cap = min(0.95, max(0.0, float(caps.get("ETH/USDT", 0.95))))
            btc_blacklist = 0.95 if btc_cap <= 0 else 0.05
            eth_blacklist = 0.95 if eth_cap <= 0 else 0.05

            with torch.no_grad():
                for module in (
                    self.portfolio_actor,
                    self.risk_actor,
                    self.critic,
                ):
                    for parameter in module.parameters():
                        parameter.zero_()

                portfolio_targets = [
                    max(1e-5, btc_cap),
                    max(1e-5, eth_cap),
                    btc_blacklist,
                    eth_blacklist,
                ]
                risk_targets = [0.50, 1.0 / 3.0, 0.05, 0.05]
                self.portfolio_actor.mean.bias.copy_(
                    torch.tensor(
                        [self._logit(v) for v in portfolio_targets],
                        dtype=torch.float32,
                    )
                )
                self.risk_actor.mean.bias.copy_(
                    torch.tensor(
                        [self._logit(v) for v in risk_targets],
                        dtype=torch.float32,
                    )
                )
                self.portfolio_actor.log_std.fill_(-2.5)
                self.risk_actor.log_std.fill_(-2.5)

            self.portfolio_actor.eval()
            self.risk_actor.eval()
            self.critic.eval()
            self.ready = True
            self.policy_version = policy_version

        def load_payload(self, payload: dict[str, Any], *, policy_version: str) -> None:
            required = {"portfolio_actor", "risk_actor", "critic"}
            if not required.issubset(payload):
                raise ValueError("incomplete MAPPO policy payload")

            def state_dict(raw: dict[str, Any]) -> dict[str, "torch.Tensor"]:
                return {
                    key: torch.as_tensor(value, dtype=torch.float32)
                    for key, value in raw.items()
                }

            self.portfolio_actor.load_state_dict(state_dict(payload["portfolio_actor"]))
            self.risk_actor.load_state_dict(state_dict(payload["risk_actor"]))
            self.critic.load_state_dict(state_dict(payload["critic"]))
            self.portfolio_actor.eval()
            self.risk_actor.eval()
            self.critic.eval()
            self.ready = True
            self.policy_version = policy_version

        @staticmethod
        def _bounded_action_with_log_prob(
            actor: _Actor,
            state: "torch.Tensor",
            *,
            stochastic: bool,
        ) -> tuple[np.ndarray, float]:
            dist = actor.distribution(state)
            latent = dist.sample() if stochastic else dist.mean
            bounded = torch.sigmoid(latent)
            log_prob = dist.log_prob(latent).sum(-1)
            return (
                bounded.squeeze(0).cpu().numpy(),
                float(log_prob.squeeze(0).cpu().item()),
            )

        def portfolio_action_with_log_prob(
            self,
            state: list[float],
            *,
            stochastic: bool = False,
        ) -> tuple[np.ndarray, float]:
            if not self.ready:
                raise MARLPolicyNotReady("MAPPO portfolio policy is not trained/loaded")
            with torch.no_grad():
                return self._bounded_action_with_log_prob(
                    self.portfolio_actor,
                    self._tensor(state),
                    stochastic=stochastic,
                )

        def risk_action_with_log_prob(
            self,
            state: list[float],
            *,
            stochastic: bool = False,
        ) -> tuple[np.ndarray, float]:
            if not self.ready:
                raise MARLPolicyNotReady("MAPPO risk policy is not trained/loaded")
            with torch.no_grad():
                return self._bounded_action_with_log_prob(
                    self.risk_actor,
                    self._tensor(state),
                    stochastic=stochastic,
                )

        def portfolio_action(self, state: list[float]) -> np.ndarray:
            action, _ = self.portfolio_action_with_log_prob(state)
            return action

        def risk_action(self, state: list[float]) -> np.ndarray:
            action, _ = self.risk_action_with_log_prob(state)
            return action

        def value(self, global_state: list[float]) -> float:
            if not self.ready:
                raise MARLPolicyNotReady("MAPPO critic is not trained/loaded")
            with torch.no_grad():
                return float(self.critic(self._tensor(global_state)).item())

        def export_payload(self) -> dict[str, Any]:
            if not self.ready:
                raise MARLPolicyNotReady("cannot export an untrained MAPPO policy")

            def encode(module: "nn.Module") -> dict[str, Any]:
                return {
                    key: value.detach().cpu().tolist()
                    for key, value in module.state_dict().items()
                }

            return {
                "portfolio_actor": encode(self.portfolio_actor),
                "risk_actor": encode(self.risk_actor),
                "critic": encode(self.critic),
            }


    class MAPPOTrainer:
        """Minimal clipped-PPO update for asynchronous offline policy workers."""

        def __init__(self, bundle: MAPPOPolicyBundle, config: PPOConfig | None = None) -> None:
            self.bundle = bundle
            self.config = config or PPOConfig()
            self.portfolio_optimizer = torch.optim.Adam(
                bundle.portfolio_actor.parameters(), lr=self.config.actor_lr
            )
            self.risk_optimizer = torch.optim.Adam(
                bundle.risk_actor.parameters(), lr=self.config.actor_lr
            )
            self.critic_optimizer = torch.optim.Adam(
                bundle.critic.parameters(), lr=self.config.critic_lr
            )

        def _actor_step(
            self,
            actor: _Actor,
            optimizer: "torch.optim.Optimizer",
            states: "torch.Tensor",
            actions: "torch.Tensor",
            old_log_probs: "torch.Tensor",
            advantages: "torch.Tensor",
        ) -> tuple[float, float]:
            dist = actor.distribution(states)
            logits = torch.logit(actions.clamp(1e-5, 1 - 1e-5))
            log_probs = dist.log_prob(logits).sum(-1)
            ratio = (log_probs - old_log_probs).exp()
            clipped = ratio.clamp(
                1.0 - self.config.clip_ratio,
                1.0 + self.config.clip_ratio,
            )
            surrogate = torch.minimum(ratio * advantages, clipped * advantages)
            entropy = dist.entropy().sum(-1).mean()
            loss = -surrogate.mean() - (self.config.entropy_coef * entropy)

            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(actor.parameters(), self.config.max_grad_norm)
            optimizer.step()
            return float(loss.detach().item()), float(entropy.detach().item())

        def update(
            self,
            *,
            portfolio_states: "torch.Tensor",
            portfolio_actions: "torch.Tensor",
            portfolio_old_log_probs: "torch.Tensor",
            risk_states: "torch.Tensor",
            risk_actions: "torch.Tensor",
            risk_old_log_probs: "torch.Tensor",
            global_states: "torch.Tensor",
            returns: "torch.Tensor",
            advantages: "torch.Tensor",
        ) -> dict[str, float]:
            portfolio_loss, portfolio_entropy = self._actor_step(
                self.bundle.portfolio_actor,
                self.portfolio_optimizer,
                portfolio_states,
                portfolio_actions,
                portfolio_old_log_probs,
                advantages,
            )
            risk_loss, risk_entropy = self._actor_step(
                self.bundle.risk_actor,
                self.risk_optimizer,
                risk_states,
                risk_actions,
                risk_old_log_probs,
                advantages,
            )

            values = self.bundle.critic(global_states)
            critic_loss = ((values - returns) ** 2).mean() * self.config.value_coef
            self.critic_optimizer.zero_grad(set_to_none=True)
            critic_loss.backward()
            torch.nn.utils.clip_grad_norm_(
                self.bundle.critic.parameters(),
                self.config.max_grad_norm,
            )
            self.critic_optimizer.step()

            return {
                "portfolio_actor_loss": portfolio_loss,
                "risk_actor_loss": risk_loss,
                "critic_loss": float(critic_loss.detach().item()),
                "entropy": (portfolio_entropy + risk_entropy) / 2.0,
            }

else:

    class MAPPOPolicyBundle:
        def __init__(self, **_: Any) -> None:
            self.ready = False
            self.policy_version = "torch_unavailable"

        def initialize_safe_prior(self, *_: Any, **__: Any) -> None:
            raise MARLDependencyError("install backend/requirements-marl.txt")

        def load_payload(self, *_: Any, **__: Any) -> None:
            raise MARLDependencyError("install backend/requirements-marl.txt")

        def portfolio_action_with_log_prob(self, *_: Any, **__: Any):
            raise MARLDependencyError("install backend/requirements-marl.txt")

        def risk_action_with_log_prob(self, *_: Any, **__: Any):
            raise MARLDependencyError("install backend/requirements-marl.txt")

        def portfolio_action(self, *_: Any, **__: Any) -> np.ndarray:
            raise MARLDependencyError("install backend/requirements-marl.txt")

        def risk_action(self, *_: Any, **__: Any) -> np.ndarray:
            raise MARLDependencyError("install backend/requirements-marl.txt")

        def value(self, *_: Any, **__: Any) -> float:
            raise MARLDependencyError("install backend/requirements-marl.txt")

        def export_payload(self) -> dict[str, Any]:
            raise MARLDependencyError("install backend/requirements-marl.txt")


    class MAPPOTrainer:
        def __init__(self, *_: Any, **__: Any) -> None:
            raise MARLDependencyError("install backend/requirements-marl.txt")
