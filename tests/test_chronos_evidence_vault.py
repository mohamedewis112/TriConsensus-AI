from triconsensus.chronos_evidence_vault import WindowEvidence, audit_windows


def _w(i:int, *, hits:int=9, n:int=12, pos:int=6, neg:int=6, brier:float=0.2):
    return WindowEvidence(
        window_id=f"w{i}",
        symbol="ETHUSDT",
        source="OKX_L2_COINBASE_SPOT",
        model_family="v8-learned-world",
        horizon_seconds=30,
        resolved_forecasts=30,
        non_overlapping_samples=n,
        non_overlapping_hits=hits,
        positive_count=pos,
        negative_or_flat_count=neg,
        brier_skill_score=brier,
        trusted_at_training=False,
    )


def test_vault_rejects_single_impressive_window():
    audit=audit_windows([_w(1,hits=11,n=12)], minimum_windows=4)
    assert audit.evidence_qualified is False
    assert "insufficient_independent_windows" in audit.reasons
    assert audit.paper_trading_authority is False


def test_vault_can_qualify_repeated_balanced_edge():
    windows=[_w(i) for i in range(5)]
    audit=audit_windows(
        windows,
        minimum_windows=4,
        minimum_non_overlapping_samples=40,
        minimum_each_class=10,
        minimum_windows_beating_base_rate=3,
    )
    assert audit.windows==5
    assert audit.pooled_non_overlapping_accuracy==0.75
    assert audit.exact_binomial_p_value < 0.05
    assert audit.evidence_qualified is True
    assert audit.live_trading_authority is False


def test_vault_rejects_source_drift_even_when_hits_are_high():
    windows=[_w(i,hits=10) for i in range(4)]
    windows[-1]=WindowEvidence(
        **{**windows[-1].__dict__,"source":"CHANGED_SOURCE"}
    )
    audit=audit_windows(windows,minimum_windows=4,minimum_non_overlapping_samples=40)
    assert audit.evidence_qualified is False
    assert "source_definition_changed_across_windows" in audit.reasons
