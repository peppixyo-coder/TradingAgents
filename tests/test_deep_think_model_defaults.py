import importlib

from tradingagents import default_config


def _reload(monkeypatch, **values):
    for name in ("UPSTREAM_LLM_MODEL", "CUSTOM_LLM_MODEL", "TRADINGAGENTS_DEEP_THINK_LLM"):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return importlib.reload(default_config).DEFAULT_CONFIG


def test_deep_default_is_combo1_and_quick_default_is_combo2(monkeypatch):
    config = _reload(monkeypatch, UPSTREAM_LLM_MODEL="Combo-2")
    assert config["deep_think_llm"] == "Combo-1"
    assert config["quick_think_llm"] == "Combo-2"



def test_operational_model_environment_keeps_quick_and_deep_separate(monkeypatch):
    config = _reload(
        monkeypatch,
        UPSTREAM_LLM_MODEL="Combo-2",
        CUSTOM_LLM_MODEL="Combo-1",
    )
    assert config["deep_think_llm"] == "Combo-1"
    assert config["quick_think_llm"] == "Combo-2"
def test_explicit_deep_override_does_not_change_quick_model(monkeypatch):
    config = _reload(
        monkeypatch,
        UPSTREAM_LLM_MODEL="Combo-2",
        TRADINGAGENTS_DEEP_THINK_LLM="custom-deep",
    )
    assert config["deep_think_llm"] == "custom-deep"
    assert config["quick_think_llm"] == "Combo-2"
