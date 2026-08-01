"""Test-session setup.

The suite must be hermetic. Once a real Azure key is configured, anything that
calls ``llm.available()`` would otherwise reach the network — making the tests
slow, costly, dependent on someone else's uptime, and non-deterministic in
exactly the dimension this system claims to be deterministic in.

So the model is disabled for the whole session. Extraction runs from recorded
payloads and explanations use the deterministic template, which is what the
tests assert against anyway.
"""

import pytest

from app import llm


@pytest.fixture(autouse=True, scope="session")
def _no_network_llm():
    original_get, original_available = llm.get_client, llm.available
    llm.get_client = lambda: None
    llm.available = lambda: False
    yield
    llm.get_client, llm.available = original_get, original_available
