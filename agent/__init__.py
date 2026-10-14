"""Triage agent package.

This package holds the agent pipeline: stage schemas, the state-machine
orchestrator, LLM clients, prompts, and guardrails.
"""

from agent.config import Settings, get_settings

__all__ = ["Settings", "get_settings"]
