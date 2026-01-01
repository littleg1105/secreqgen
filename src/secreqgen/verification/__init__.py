"""Verification module for LLM-as-Judge quality assurance.

This module provides functionality to evaluate and filter generated security
requirements using a secondary LLM pass (the "judge") to score each requirement
for relevance, specificity, and actionability.
"""

from secreqgen.verification.judge import RequirementJudge, JudgmentResult

__all__ = ["RequirementJudge", "JudgmentResult"]
