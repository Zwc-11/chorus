"""Tool gateway exports.

The gateway is the single choke point for tool calls. Exporting it here keeps
imports short for code that needs record/replay behavior.
"""

from murmur.gateway.tool_gateway import ReplayDivergenceError, ToolGateway

__all__ = ["ReplayDivergenceError", "ToolGateway"]
