"""Runtime guards for the OpenAI-compatible client import and payload hook."""

import pytest
from langchain_core.messages import HumanMessage


@pytest.mark.unit
def test_client_import_logger_and_minimal_payload_without_network():
    from tradingagents.llm_clients import openai_client as client

    assert client.logger.name == client.__name__
    payload = client.NormalizedChatOpenAI.__new__(client.NormalizedChatOpenAI)
    payload._get_request_payload([HumanMessage(content="hello")])
