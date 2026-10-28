from __future__ import annotations

from .regime_replication import compare_regime_reports


def compare_setup_reports(
    discovery: dict,
    replication: dict,
) -> dict:
    discovery_version = str(discovery.get("setup_version") or "")
    replication_version = str(replication.get("setup_version") or "")
    if not discovery_version or discovery_version != replication_version:
        raise ValueError("setup versions must match for replication")

    policy = compare_regime_reports(discovery, replication)
    policy["policy_type"] = "V4_REPLICATED_SETUP_POLICY"
    policy["setup_version"] = discovery_version
    policy["default_action"] = "NO_TRADE"
    return policy
