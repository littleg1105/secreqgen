"""Read a model answer and validate it as the old generator does. Failures are counted, never hidden."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

from secreqgen.generation.generator import SecurityRequirementsGenerator
from secreqgen.generation.schemas import (
    ASVSCategory,
    SecurityRequirement,
    ThreatAgentMapping,
    UserStory,
)

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
_OBJECT_RE = re.compile(r"\{[\s\S]*\}")
_V1_RE = re.compile(r"v?1\b")
_V1_WORDS = ("architecture", "design", "threat")
_MAX_ERR = 300


class UnreadableAnswer(ValueError):
    """No JSON object could be read from the answer. `errors` holds one message per step."""

    def __init__(self, errors: Iterable[str]) -> None:
        self.errors: tuple[str, ...] = tuple(errors)
        super().__init__("the answer is not readable: " + " | ".join(self.errors))


class NoRequirementsList(ValueError):
    """The JSON object is fine, but 'requirements' is missing or is not a list."""


@dataclass(frozen=True)
class ExtractResult:
    """A JSON object read from an answer and the way it was found."""

    data: dict
    method: str
    parsed_by_fallback: bool


@dataclass(frozen=True)
class ValidationFailure:
    """One item that failed validation."""

    index: int
    error: str


@dataclass(frozen=True)
class ParsedAnswer:
    """Result of parse_answer."""

    requirements: tuple[SecurityRequirement, ...]
    threat_mappings: tuple[ThreatAgentMapping, ...]
    n_requirements_raw: int
    n_requirements_failed_validation: int
    validation_failures: tuple[ValidationFailure, ...]
    n_threat_mappings_raw: int
    n_threat_mappings_failed: int
    asvs_defaulted: int
    method: str
    parsed_by_fallback: bool
    old_parser_result: str


def _cut(text: str) -> str:
    return text if len(text) <= _MAX_ERR else text[:_MAX_ERR]


def extract_json(text: str) -> ExtractResult:
    """Read a JSON object: whole text, then first-to-last brace, then code fences."""
    errors: list[str] = []
    try:
        value = json.loads(text.strip())
        if isinstance(value, dict):
            return ExtractResult(value, "whole", False)
        errors.append(f"whole: the JSON value is a {type(value).__name__}, not an object")
    except json.JSONDecodeError as exc:
        errors.append(_cut(f"whole: {exc}"))

    first, last = text.find("{"), text.rfind("}")
    if first == -1 or last <= first:
        errors.append("object: no pair of braces")
    else:
        try:
            value = json.loads(text[first : last + 1])
            if isinstance(value, dict):
                return ExtractResult(value, "object", True)
            errors.append(f"object: the JSON value is a {type(value).__name__}")
        except json.JSONDecodeError as exc:
            errors.append(_cut(f"object: {exc}"))

    blocks = _FENCE_RE.findall(text)
    if not blocks:
        errors.append("fence: no code fence")
    else:
        fence_errors: list[str] = []
        for i, block in enumerate(blocks):
            try:
                value = json.loads(block)
            except json.JSONDecodeError as exc:
                fence_errors.append(f"block {i + 1}: {exc}")
                continue
            if isinstance(value, dict):
                return ExtractResult(value, "fence", True)
            fence_errors.append(f"block {i + 1}: not an object")
        errors.append(_cut("fence: " + "; ".join(fence_errors)))
    raise UnreadableAnswer(errors)


def old_order_extract(text: str) -> dict:
    """Copy of the old extraction order (fence, object, whole). For diagnosis only."""
    m = _FENCE_RE.search(text)
    if m:
        return json.loads(m.group(1).strip())
    m = _OBJECT_RE.search(text)
    if m:
        return json.loads(m.group(0))
    return json.loads(text.strip())


def compare_with_old(text: str, new_data: dict) -> str:
    """Return 'same', 'different' or 'failed' for the old order against the new result."""
    try:
        old = old_order_extract(text)
    except Exception:  # any failure of the old order is the answer "failed"
        return "failed"
    return "same" if old == new_data else "different"


class _Validator(SecurityRequirementsGenerator):
    """The old generator used only for its validation code. It has no client."""

    def __init__(self) -> None:
        super().__init__(llm_client=None, prompt_builder=object())  # type: ignore[arg-type]
        self.defaulted = 0

    def _normalize_asvs_category(self, value: str) -> ASVSCategory:
        result = super()._normalize_asvs_category(value)
        if result == ASVSCategory.V1_ARCHITECTURE:
            low = value.lower().strip()
            if not _V1_RE.match(low) and not any(w in low for w in _V1_WORDS):
                self.defaulted += 1
        return result


def parse_answer(text: str, story_id: str) -> ParsedAnswer:
    """Extract and validate the requirements of an answer."""
    ext = extract_json(text)
    data = ext.data
    raw_reqs = data.get("requirements")
    if not isinstance(raw_reqs, list):
        raise NoRequirementsList(
            f"'requirements' is {'missing' if 'requirements' not in data else type(raw_reqs).__name__}"
        )
    validator = _Validator()
    story = UserStory(id=story_id, title="", story="")
    reqs: list[SecurityRequirement] = []
    failures: list[ValidationFailure] = []
    for i, raw in enumerate(raw_reqs):
        try:
            if not isinstance(raw, dict):
                raise TypeError(f"item is a {type(raw).__name__}, not an object")
            reqs.append(validator._parse_requirement(raw, story, i))
        except Exception as exc:  # counted below, never dropped silently
            failures.append(ValidationFailure(i, _cut(f"{type(exc).__name__}: {exc}")))

    threats: list[ThreatAgentMapping] = []
    n_threat_raw = 0
    n_threat_failed = 0
    if "threat_mappings" in data:
        raw_threats = data["threat_mappings"]
        if not isinstance(raw_threats, list):
            n_threat_failed = 1
        else:
            n_threat_raw = len(raw_threats)
            for raw in raw_threats:
                try:
                    if not isinstance(raw, dict):
                        raise TypeError("item is not an object")
                    threats.append(validator._parse_threat_mapping(raw))
                except Exception:
                    n_threat_failed += 1

    return ParsedAnswer(
        requirements=tuple(reqs),
        threat_mappings=tuple(threats),
        n_requirements_raw=len(raw_reqs),
        n_requirements_failed_validation=len(failures),
        validation_failures=tuple(failures),
        n_threat_mappings_raw=n_threat_raw,
        n_threat_mappings_failed=n_threat_failed,
        asvs_defaulted=validator.defaulted,
        method=ext.method,
        parsed_by_fallback=ext.parsed_by_fallback,
        old_parser_result=compare_with_old(text, data),
    )


def requirement_dicts(requirements: Iterable[SecurityRequirement]) -> list[dict]:
    """Requirements as JSON-safe dicts, as run_study2.py does."""
    return [r.model_dump(mode="json") for r in requirements]
