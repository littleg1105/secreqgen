"""System context of a codebase, written by a model and checked by code."""

from secreqgen.context.about import (
    ABOUT_BUDGET,
    ABOUT_DRAFT_NOTICE,
    ABOUT_FILE_NOTICE,
    AboutDraft,
    about_from_context,
    about_from_file_data,
    about_paragraph,
)
from secreqgen.context.pipeline import DRAFT_NOTICE, SystemContextWriter
from secreqgen.context.render import render_yaml, source_lines
from secreqgen.context.scan import (
    CodebaseFacts,
    ContextError,
    Part,
    scan_codebase,
)

__all__ = [
    "ABOUT_BUDGET",
    "ABOUT_DRAFT_NOTICE",
    "ABOUT_FILE_NOTICE",
    "DRAFT_NOTICE",
    "AboutDraft",
    "CodebaseFacts",
    "ContextError",
    "Part",
    "SystemContextWriter",
    "about_from_context",
    "about_from_file_data",
    "about_paragraph",
    "render_yaml",
    "scan_codebase",
    "source_lines",
]
