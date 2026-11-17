"""Other documentation file types become Markdown text.

The search of the tool reads `.md` files only. This package makes a mirror
folder in which every other file type is also a `.md` file. No model and no
network is used here.
"""

from secreqgen.documents.formats import OTHER_SUFFIXES, FormatError, to_markdown
from secreqgen.documents.mirror import (
    MAX_FILE_BYTES,
    SKIPPED_FOLDERS,
    MirrorReport,
    build_mirror,
    other_files,
)

__all__ = [
    "MAX_FILE_BYTES",
    "OTHER_SUFFIXES",
    "SKIPPED_FOLDERS",
    "FormatError",
    "MirrorReport",
    "build_mirror",
    "other_files",
    "to_markdown",
]
