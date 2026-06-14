"""Agent adapter exports.

Agent adapters know how to drive one kind of agent under test. Right now we
export the fake deterministic agent used for local demos and tests.
"""

from murmur.adapters.agents.fake import FakeAgent, fake_tools

__all__ = ["FakeAgent", "fake_tools"]
