"""Check the prompt folders of Study 2b (data/winning_prompts_s2b/).

Usage (from coded_project/secreqgen):
    PYTHONPATH=src .venv/bin/python scripts/check_s2b_prompts.py

The script prints one PASS or FAIL line for each check. Then it prints the
"residue": each line of the n1 and n0 files that holds one of the scan words.
The residue is for the dated note (lab notebook section 16). Printing it is not
a failure. The exit code is 1 when one check fails, else 0.

What the four folders must hold (plan section 6, analysis lock section 8):
  rep  Byte copies of the three registered files. Arms A2 and B2 (the drift check).
  i0   Byte copies of the three registered files. Arm I0: the arm B prompt with
       one fixed line in place of the stored passages.
  n1   system_prompt.txt and zero_shot_user_prompt.txt are byte copies of the
       registered files. rag_user_prompt.txt is the registered zero-shot file
       plus one plain documentation block, at the place where the registered
       rag template has its documentation block. Nothing else differs.
  n0   The n1 files with five deletions: S1, S2 and S3 in the system prompt,
       U0 and U1 in the two user templates.

The registered files are in data/winning_prompts_idea21/. This script only
reads files. It writes nothing. It uses the Python standard library only.
"""

from __future__ import annotations

import difflib
import hashlib
import re
import sys
from pathlib import Path
from string import Template

SECREQGEN = Path(__file__).resolve().parents[1]
REGISTERED = SECREQGEN / "data" / "winning_prompts_idea21"
S2B = SECREQGEN / "data" / "winning_prompts_s2b"
FILES = ("system_prompt.txt", "zero_shot_user_prompt.txt", "rag_user_prompt.txt")
FOLDERS = ("n1", "i0", "n0", "rep")
SUMS_NAME = "SHA256SUMS"

# The one block that n1 (and n0) add to the zero-shot template. The final empty
# line keeps one blank line before "## User Story to Analyze", as in the
# registered rag template.
BLOCK = (
    "## Project Documentation\n"
    "The passages below come from the project's own documentation and source code.\n"
    "\n"
    "$retrieved_context\n"
    "\n"
    "---\n"
    "\n"
)
# The block goes just before this line. In the registered rag template the
# documentation block is in the same place: after the system context, before
# the user story.
ANCHOR = "## User Story to Analyze\n"
BEFORE_BLOCK = "$system_context\n\n"
REGISTERED_BLOCK_HEAD = "## Project Security Documentation (CRITICAL - You MUST use this)\n"

# The sentence that must be gone from n0 (plan section 6).
TARGET_SENTENCE = "If the docs name a configuration key, cite that key by name"

# The four deletions of the design (designs["design:instruction_control"]): S1, S2, S3 and U1.
# A fifth one, U0, was added on 14 November 2026 after the read of the prompt differences by a second model.
# S1: system prompt, task sentence. The sentence then ends after "user stories".
S1 = (" AND the project-specific terminology that appears in the project's own documentation", "")
# S2: system prompt, guideline 5.
S2 = (
    "5. CITE sources - reference ASVS requirements, security standards, or provided documentation\n",
    "5. CITE sources - reference ASVS requirements or security standards\n",
)
# S3: system prompt, the whole "GROUNDED CITATION RULE (CRITICAL)" block: the
# header, its four bullets and the blank line after them.
S3_START = "GROUNDED CITATION RULE (CRITICAL):\n"
S3_END = "Always respond with valid JSON"
S3_LINES = 6
# U0: user templates, line 1 (the heading of the examples). The design did not
# name it. The second model that read the prompt differences on 14 November 2026
# found it: the heading still said that the examples show "project-specific
# citation". The heading then ends "ASVS mapping)".
U0 = (", AND project-specific citation)\n", ")\n")
# U1: user templates, line 3. Two parts. (a) delete the clause about how to
# cite tokens; (b) delete from ", and replace them with the actual tokens" to
# the end of the sentence. The sentence then ends
# "Do not copy their content; use them to calibrate the form."
U1A = (", AND how to cite project-specific tokens by name", "")
U1B_START = ", and replace them with the actual tokens"
U1_LINE_AFTER_END = "Do not copy their content; use them to calibrate the form.\n"

# Words for the residue scan. A word matches at the start of a word, in upper
# or lower case. "cita" and "citing" are added because "cite" does not match
# "citation" or "citing". "use" does not match "user", "users" or "user_story_id".
RESIDUE_PATTERN = re.compile(
    r"\b(must|cite|cita|citing|prioriti|grounded|first|token|prefer)|\buse(?!r)",
    re.IGNORECASE,
)


class PromptError(Exception):
    """A change to a template cannot be made in the exact way the design says."""


# ---------------------------------------------------------------------------
# The changes, as functions. The build step and the checks use the same text.
# ---------------------------------------------------------------------------
def _replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise PromptError(f"{label}: the text to change occurs {n} times, not 1 time")
    return text.replace(old, new, 1)


def add_block(zero_shot: str) -> str:
    """The registered zero-shot template plus the one block, before ANCHOR."""
    if zero_shot.count(ANCHOR) != 1:
        raise PromptError(f"the line {ANCHOR.strip()!r} must occur 1 time")
    i = zero_shot.index(ANCHOR)
    if not zero_shot[:i].endswith(BEFORE_BLOCK):
        raise PromptError("the system context is not just before the user story")
    return zero_shot[:i] + BLOCK + zero_shot[i:]


def delete_s3(system: str) -> str:
    if system.count(S3_START) != 1 or system.count(S3_END) != 1:
        raise PromptError("S3: the start or the end of the citation rule block is not there 1 time")
    i, j = system.index(S3_START), system.index(S3_END)
    span = system[i:j]
    if j < i or span.count("\n") != S3_LINES or TARGET_SENTENCE not in span:
        raise PromptError("S3: the citation rule block does not have the expected six lines")
    return system[:i] + system[j:]


def delete_u1(user: str) -> str:
    out = _replace_once(user, U1A[0], U1A[1], "U1 (a)")
    if out.count(U1B_START) != 1:
        raise PromptError("U1 (b): the clause start does not occur 1 time")
    i = out.index(U1B_START)
    j = out.index(".\n", i)  # the full stop at the end of line 3
    if "\n" in out[i:j]:
        raise PromptError("U1 (b): the clause does not end on its own line")
    out = out[:i] + out[j:]
    if out.count(U1_LINE_AFTER_END) != 1:
        raise PromptError("U1: line 3 does not end as the design says")
    return out


def n0_system(n1_system: str) -> str:
    out = _replace_once(n1_system, S1[0], S1[1], "S1")
    out = _replace_once(out, S2[0], S2[1], "S2")
    return delete_s3(out)


def n0_user(n1_user: str) -> str:
    return delete_u1(_replace_once(n1_user, U0[0], U0[1], "U0"))


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def placeholders(text: str) -> tuple[set[str], bool]:
    t = Template(text)
    return set(t.get_identifiers()), t.is_valid()


def changed_lines(old: str, new: str) -> list[tuple[str, int, int, int, int]]:
    """The non-equal opcodes of a line diff (tag, i1, i2, j1, j2), 0-based."""
    sm = difflib.SequenceMatcher(a=old.splitlines(keepends=True), b=new.splitlines(keepends=True),
                                 autojunk=False)
    return [op for op in sm.get_opcodes() if op[0] != "equal"]


def run_checks(s2b: Path | None = None, registered: Path | None = None) -> list[tuple[bool, str, str]]:
    """Each check as (ok, name, detail). The detail names files, never a result."""
    s2b = s2b or S2B
    registered = registered or REGISTERED
    out: list[tuple[bool, str, str]] = []

    def add(ok: bool, name: str, detail: str = "") -> None:
        out.append((bool(ok), name, detail))

    reg = {}
    for f in FILES:
        p = registered / f
        if not p.is_file():
            add(False, f"registered file {f} exists", str(p))
            return out
        reg[f] = p.read_bytes()

    have = {}
    for folder in FOLDERS:
        d = s2b / folder
        missing = [f for f in FILES if not (d / f).is_file()]
        add(not missing, f"{folder}/ holds the three files", "missing: " + ", ".join(missing) if missing else "")
        for f in FILES:
            if (d / f).is_file():
                have[(folder, f)] = (d / f).read_bytes()
    if len(have) != len(FOLDERS) * len(FILES):
        return out

    def text(folder: str, f: str) -> str:
        return have[(folder, f)].decode("utf-8")

    reg_txt = {f: reg[f].decode("utf-8") for f in FILES}

    for folder in ("rep", "i0"):
        for f in FILES:
            add(have[(folder, f)] == reg[f], f"{folder}/{f} equals the registered file byte for byte")
    for f in ("system_prompt.txt", "zero_shot_user_prompt.txt"):
        add(have[("n1", f)] == reg[f], f"n1/{f} equals the registered file byte for byte")

    # n1 rag = registered zero-shot + the one block, nothing else.
    try:
        expected = add_block(reg_txt["zero_shot_user_prompt.txt"])
        add(text("n1", "rag_user_prompt.txt") == expected,
            "n1/rag_user_prompt.txt equals the registered zero-shot template plus the one block")
    except PromptError as e:
        add(False, "n1/rag_user_prompt.txt equals the registered zero-shot template plus the one block", str(e))
    ops = changed_lines(reg_txt["zero_shot_user_prompt.txt"], text("n1", "rag_user_prompt.txt"))
    block_lines = BLOCK.splitlines(keepends=True)
    n1_lines = text("n1", "rag_user_prompt.txt").splitlines(keepends=True)
    one_insert = len(ops) == 1 and ops[0][0] == "insert" and n1_lines[ops[0][3]:ops[0][4]] == block_lines
    add(one_insert, "line diff of n1 rag against the registered zero-shot template is one insert of the block",
        "" if one_insert else f"opcodes: {[op[0] for op in ops]}")
    # Same place as the registered documentation block.
    reg_rag = reg_txt["rag_user_prompt.txt"]
    ok_place = False
    if reg_rag.count(REGISTERED_BLOCK_HEAD) == 1:
        k = reg_rag.index(REGISTERED_BLOCK_HEAD)
        after = reg_rag[k:]
        ok_place = (reg_rag[:k].endswith(BEFORE_BLOCK)
                    and after.split("$retrieved_context\n\n---\n\n", 1)[-1].startswith(ANCHOR))
        n1_rag = text("n1", "rag_user_prompt.txt")
        k1 = n1_rag.find(BLOCK)
        ok_place = ok_place and k1 > 0 and n1_rag[:k1].endswith(BEFORE_BLOCK) and \
            n1_rag[k1 + len(BLOCK):].startswith(ANCHOR)
    add(ok_place, "the n1 block is in the place of the registered documentation block "
                  "(after the system context, before the user story)")

    # n0 = n1 with S1, S2, S3, U0 and U1, nothing else.
    for f, fn in (("system_prompt.txt", n0_system), ("zero_shot_user_prompt.txt", n0_user),
                  ("rag_user_prompt.txt", n0_user)):
        try:
            add(text("n0", f) == fn(text("n1", f)), f"n0/{f} equals n1/{f} with the deletions "
                + ("S1, S2 and S3" if f == "system_prompt.txt" else "U0 and U1") + " only")
        except PromptError as e:
            add(False, f"n0/{f} equals n1/{f} with the deletions only", str(e))
    # Independent line check: which lines of n1 changed in n0.
    ops = changed_lines(text("n1", "system_prompt.txt"), text("n0", "system_prompt.txt"))
    shape = [(op[0], op[1] + 1, op[2]) for op in ops]  # 1-based first line, last line
    expected_shape = [("replace", 7, 7), ("replace", 14, 14), ("delete", 17, 22)]
    add(shape == expected_shape, "n0 system prompt changes only lines 7 (S1), 14 (S2) and 17 to 22 (S3) of n1",
        "" if shape == expected_shape else f"changed: {shape}")
    for f in ("zero_shot_user_prompt.txt", "rag_user_prompt.txt"):
        ops = changed_lines(text("n1", f), text("n0", f))
        shape = [(op[0], op[1] + 1, op[2]) for op in ops]
        want = [("replace", 1, 1), ("replace", 3, 3)]
        add(shape == want, f"n0/{f} changes only line 1 (U0) and line 3 (U1) of n1",
            "" if shape == want else f"changed: {shape}")

    # Placeholders.
    for folder in FOLDERS:
        rag = text(folder, "rag_user_prompt.txt")
        n = rag.count("$retrieved_context") + rag.count("${retrieved_context}")
        add(n == 1, f"{folder}/rag_user_prompt.txt holds $retrieved_context exactly one time",
            "" if n == 1 else f"found {n} times")
        for f in FILES:
            ids, valid = placeholders(text(folder, f))
            reg_ids, _ = placeholders(reg_txt[f])
            if f == "zero_shot_user_prompt.txt" or (folder in ("n1", "n0") and f == "rag_user_prompt.txt"):
                # The n1 and n0 rag templates are zero-shot templates plus $retrieved_context.
                base = placeholders(reg_txt["zero_shot_user_prompt.txt"])[0]
                want = base | ({"retrieved_context"} if f == "rag_user_prompt.txt" else set())
            else:
                want = reg_ids
            ok = valid and ids == want
            detail = "" if ok else f"missing: {sorted(want - ids)}, extra: {sorted(ids - want)}, valid: {valid}"
            add(ok, f"{folder}/{f} keeps each placeholder of its registered base and adds none", detail)
    reg_rag_ids = placeholders(reg_rag)[0]
    for folder in FOLDERS:
        ids = placeholders(text(folder, "rag_user_prompt.txt"))[0]
        add(reg_rag_ids <= ids, f"{folder}/rag_user_prompt.txt holds each placeholder of the registered rag template")

    # The target sentence is gone from n0.
    for f in FILES:
        add(TARGET_SENTENCE not in text("n0", f), f"n0/{f} does not hold the sentence \"{TARGET_SENTENCE}\"")
    add(S3_START.strip() not in text("n0", "system_prompt.txt"),
        "n0/system_prompt.txt does not hold the GROUNDED CITATION RULE block")

    # SHA256SUMS.
    sums = s2b / SUMS_NAME
    if not sums.is_file():
        add(False, f"{SUMS_NAME} exists and matches the twelve files", "file missing")
    else:
        rows = {}
        for line in sums.read_text().splitlines():
            if line.strip():
                h, _, name = line.partition("  ")
                rows[name.strip()] = h.strip()
        want = {f"{folder}/{f}" for folder in FOLDERS for f in FILES}
        bad = sorted(n for n in want if rows.get(n) != sha256_file(s2b / n))
        extra = sorted(set(rows) - want)
        ok = not bad and not extra
        add(ok, f"{SUMS_NAME} matches the twelve files",
            "" if ok else f"differs: {bad}; extra rows: {extra}")
    return out


def _region(lines: list[str], idx: int) -> str:
    """'exemplar' for lines inside the two few-shot examples, else 'instruction text'."""
    start = next((i for i, x in enumerate(lines) if x.startswith("### Example 1")), None)
    end = next((i for i, x in enumerate(lines) if x.startswith("## Now generate requirements")), None)
    if start is not None and end is not None and start <= idx < end:
        return "exemplar"
    return "instruction text"


def residue(s2b: Path | None = None) -> list[tuple[str, int, str, str]]:
    """(file, line number, region, line) for each line of n1 and n0 with a scan word."""
    s2b = s2b or S2B
    rows = []
    for folder in ("n1", "n0"):
        for f in FILES:
            p = s2b / folder / f
            if not p.is_file():
                continue
            lines = p.read_text().splitlines()
            for i, line in enumerate(lines):
                if RESIDUE_PATTERN.search(line):
                    rows.append((f"{folder}/{f}", i + 1, _region(lines, i), line))
    return rows


def main(argv: list[str] | None = None) -> int:
    results = run_checks()
    for ok, name, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    n_fail = sum(1 for ok, _, _ in results if not ok)
    print()
    print("Residue: lines of n1 and n0 that hold one of the words must, cite, cita, citing, prioriti,")
    print("grounded, first, token, use, prefer (at the start of a word; 'use' does not match 'user').")
    print("This list is for the note. It is not a failure.")
    for f, n, region, line in residue():
        print(f"  residue  {f}:{n}  [{region}]  {line}")
    print()
    print(f"{len(results) - n_fail} checks pass, {n_fail} fail.")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
