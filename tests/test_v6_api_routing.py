from triconsensus import api
from triconsensus.cycle import V6_PAPER_EXPERIMENT_VERSION
from triconsensus.v51_forward import PAPER_EXPERIMENT_VERSION


def test_api_defaults_to_v51_when_v6_observation_and_enforcement_disabled(monkeypatch):
    monkeypatch.setattr(api.settings, "v6_marl_observe", False)
    monkeypatch.setattr(api.settings, "v6_marl_enabled", False)
    assert api._resolved_paper_version(None) == PAPER_EXPERIMENT_VERSION


def test_api_defaults_to_v6_shadow_when_observation_enabled(monkeypatch):
    monkeypatch.setattr(api.settings, "v6_marl_observe", True)
    monkeypatch.setattr(api.settings, "v6_marl_enabled", False)
    assert api._resolved_paper_version(None) == V6_PAPER_EXPERIMENT_VERSION


def test_api_defaults_to_v6_when_enforcement_flag_enabled(monkeypatch):
    monkeypatch.setattr(api.settings, "v6_marl_observe", False)
    monkeypatch.setattr(api.settings, "v6_marl_enabled", True)
    assert api._resolved_paper_version(None) == V6_PAPER_EXPERIMENT_VERSION


def test_explicit_version_always_wins(monkeypatch):
    monkeypatch.setattr(api.settings, "v6_marl_observe", True)
    monkeypatch.setattr(api.settings, "v6_marl_enabled", False)
    assert api._resolved_paper_version("V5.1-PAPER") == "V5.1-PAPER"
