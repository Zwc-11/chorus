"""Trace projection: turn the recorded event log into OTel-shaped spans.

The event log is the source of truth (Phase 0). This package is a pure, derived
*projection* of that log into spans using the OpenTelemetry GenAI semantic
conventions (``gen_ai.*``) plus Chorus-specific ``chorus.*`` attributes. It sits
outside the execution path: it only reads events, it never drives the agent.
"""
