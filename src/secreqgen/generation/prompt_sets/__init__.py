"""Prompt sets that ship with the tool.

TESTED_PROMPT_SET is the prompt of the tested configuration "s1_word_3000"
(the final test of 17 November 2026). Its three files are byte copies of
scripts/tuning_lib/prompt_versions/s1_word_3000/. Use it with
SecurityRequirementsPromptBuilder(prompts_dir=TESTED_PROMPT_SET) and the
word search.
"""

from pathlib import Path

TESTED_PROMPT_SET: Path = Path(__file__).parent / "s1_word_3000"

__all__ = ["TESTED_PROMPT_SET"]

# The prompt builder falls back to an older built-in template when a file is missing, and says nothing.
# So a missing file must stop the program here.
PROMPT_FILES = ("system_prompt.txt", "rag_user_prompt.txt", "zero_shot_user_prompt.txt")
_missing = [name for name in PROMPT_FILES if not (TESTED_PROMPT_SET / name).is_file()]
if _missing:
    raise FileNotFoundError(f"the tested prompt set is not complete, missing: {_missing}")
