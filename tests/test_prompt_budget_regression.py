import json

import pytest

from tradingagents.llm_clients.openai_client import (
    PROMPT_TOKEN_BUDGET,
    PromptBudgetExceeded,
    _fit_prompt_budget,
)


def oversized_market_analyst_payload():
    return {
        "asset": "ADA",
        "side": "long",
        "price": 1.0,
        "indicators": [{"timestamp": i, "value": "x" * 100} for i in range(1_000)],
        "constraints": {"risk": "x" * 100},
    }


def test_market_analyst_20536_equivalent_structured_payload_fits_budget():
    messages = [{"role": "user", "content": json.dumps(oversized_market_analyst_payload())}]
    fitted = _fit_prompt_budget(messages)
    assert sum(len(str(item["content"])) for item in fitted) <= PROMPT_TOKEN_BUDGET * 4
    parsed = json.loads(fitted[0]["content"])
    assert parsed["asset"] == "ADA"
    assert parsed["side"] == "long"


def test_heterogeneous_list_is_reduced_instead_of_rejected():
    value = {"indicators": [1, "x" * 100_000], "asset": "ADA"}
    fitted = _fit_prompt_budget([{"role": "user", "content": json.dumps(value)}])
    parsed = json.loads(fitted[0]["content"])
    assert parsed["asset"] == "ADA"
    assert len(json.dumps(parsed, separators=(",", ":"))) <= PROMPT_TOKEN_BUDGET * 4

def test_one_element_top_level_list_is_reduced_instead_of_rejected():
    value = ["x" * 100_000]
    fitted = _fit_prompt_budget([{"role": "user", "content": json.dumps(value)}])
    parsed = json.loads(fitted[0]["content"])
    assert isinstance(parsed, list)
    assert len(json.dumps(parsed, separators=(",", ":"))) <= PROMPT_TOKEN_BUDGET * 4

def test_irreducible_payload_is_blocked_before_network():
    with pytest.raises(PromptBudgetExceeded):
        _fit_prompt_budget([{"role": "user", "content": json.dumps({"asset": "x" * 100_000})}])
