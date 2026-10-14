"""Versioned prompts.

Prompts are code. Each prompt has an explicit ``PROMPT_VERSION`` and is stored
here rather than inline, so eval runs can pin and diff prompt versions.
"""

from agent.prompts.classify import CLASSIFY_PROMPT_VERSION, CLASSIFY_SYSTEM, classify_user
from agent.prompts.decide import DECIDE_PROMPT_VERSION, DECIDE_SYSTEM, decide_user
from agent.prompts.draft import DRAFT_PROMPT_VERSION, DRAFT_SYSTEM, draft_user

__all__ = [
    "CLASSIFY_PROMPT_VERSION",
    "CLASSIFY_SYSTEM",
    "classify_user",
    "DECIDE_PROMPT_VERSION",
    "DECIDE_SYSTEM",
    "decide_user",
    "DRAFT_PROMPT_VERSION",
    "DRAFT_SYSTEM",
    "draft_user",
]
