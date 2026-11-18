"""One call of the writer model turns the facts of a codebase into a draft."""

from __future__ import annotations

import logging
import string
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from secreqgen.context.checks import check_answer
from secreqgen.context.render import render_yaml
from secreqgen.context.scan import CodebaseFacts, ContextError, scan_codebase

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).parent / "prompts"
MAX_JSON_ATTEMPTS = 3
DRAFT_NOTICE = (
    "DRAFT for a person to check. A model wrote this system context from the "
    "files of the product. Check each line against its source before you use it."
)
StageCallback = Callable[[str, str, dict], None]


def _evidence(facts: CodebaseFacts) -> str:
    blocks = [f"### [{p.ref}] {p.path} ({p.kind})\n{p.text}" for p in facts.parts]
    return "\n\n---\n\n".join(blocks)


class SystemContextWriter:
    """Writes a draft system context for a codebase folder."""

    def __init__(self, llm_client: Any) -> None:
        self.llm = llm_client
        self._system = (PROMPT_DIR / "system_prompt.txt").read_text(encoding="utf-8")
        self._user = string.Template(
            (PROMPT_DIR / "user_prompt.txt").read_text(encoding="utf-8")
        )

    def generate(
        self,
        folder: Path,
        product: str | None = None,
        on_stage: StageCallback | None = None,
    ) -> dict:
        """Scan the folder, ask the model one time, check and render the answer."""
        from secreqgen.threats.checks import extract_json  # noqa: PLC0415

        def emit(stage: str, state: str, info: dict) -> None:
            if on_stage is None:
                return
            try:
                on_stage(stage, state, info)
            except Exception:  # noqa: BLE001
                logger.debug("context: a stage callback failed")

        start = time.time()
        emit("scan", "start", {})
        facts = scan_codebase(folder)
        emit("scan", "done", {"files": facts.files_seen, "parts": len(facts.parts)})
        name = (product or "").strip() or None
        line = f"The person calls the product: {name}" if name else ""
        prompt = self._user.substitute(evidence=_evidence(facts), product_line=line)
        context: dict = {}
        dropped: list[dict] = []
        accepted = readable = False
        attempts = 0
        t0 = time.time()
        for attempts in range(1, MAX_JSON_ATTEMPTS + 1):
            emit("write", "start", {"attempt": attempts})
            try:
                response = self.llm.generate(
                    prompt=prompt, system_prompt=self._system, format="json"
                )
                candidate = extract_json(response.content)
            except Exception as exc:  # noqa: BLE001
                logger.warning("context: a model call failed (%s)", type(exc).__name__)
                candidate = None
            answered = int(bool(candidate))
            emit("write", "done", {"attempt": attempts, "answered": answered})
            if not candidate:
                continue
            readable = True
            context, dropped = check_answer(candidate, facts, name)
            if context["features"] or context["tech_stack"]:
                accepted = True
                break  # else: ask again, the answer held nothing of use
        calls = [
            {
                "attempts": attempts,
                "ok": accepted,
                "seconds": round(time.time() - t0, 1),
            }
        ]
        if not accepted:
            if readable:
                raise ContextError("The model named nothing that the files support.")
            raise ContextError("The model gave no answer that can be read.")
        kept = (
            len(context["tech_stack"])
            + sum(len(g["items"]) for g in context["features"])
            + sum(len(v) for v in context["data_sensitivity"].values())
        )
        emit("check", "done", {"kept": kept, "dropped": len(dropped)})
        model_name = getattr(self.llm, "model_name", None)
        return {
            "draft_notice": DRAFT_NOTICE,
            "folder_name": facts.name,
            "model": model_name if isinstance(model_name, str) else None,
            "seconds": round(time.time() - start),
            "calls": calls,
            "facts": {
                "files_seen": facts.files_seen,
                "stopped_early": facts.stopped_early,
                "secret_files_skipped": facts.secret_files_skipped,
                "languages": facts.languages,
                "parts": [
                    {
                        "ref": p.ref,
                        "path": p.path,
                        "kind": p.kind,
                        "words": len(p.text.split()),
                    }
                    for p in facts.parts
                ],
            },
            "context": context,
            "dropped": dropped,
            "yaml": render_yaml(context),
        }
