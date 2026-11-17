"""System context of a codebase, written by a model and checked by code."""

from secreqgen.context.pipeline import DRAFT_NOTICE, SystemContextWriter
from secreqgen.context.render import render_yaml, source_lines
from secreqgen.context.scan import (
    CodebaseFacts,
    ContextError,
    Part,
    scan_codebase,
)

__all__ = [
    "DRAFT_NOTICE",
    "CodebaseFacts",
    "ContextError",
    "Part",
    "SystemContextWriter",
    "render_yaml",
    "scan_codebase",
    "source_lines",
]
