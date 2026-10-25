"""Bootstrap a thesis-evaluation dataset from a single GitHub repo URL.

Given a GitHub URL (or `owner/repo` slug), produces in
real_test_cases/<slug>/:

  user_stories.json    — N user stories extracted via Ollama from README/SECURITY
  ground_truth.json    — ASVS-derived requirements per story (rule-based, no LLM)
  system_context.yaml  — auto-built from README/package files
  docs/                — optional: cloned SECURITY.md + similar for KB ingest

Idempotent: re-running with --force overwrites; otherwise skips existing
projects.

Usage:
    python scripts/bootstrap_dataset.py owner/repo [owner/repo ...] [--force]

Or driven by a file of repo slugs (one per line, '#' for comments):
    python scripts/bootstrap_dataset.py --from scripts/repo_targets.txt
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

# Make the script independent of cwd: the script lives at
# secreqgen/scripts/, so the project root is its grandparent.
SCRIPTS_DIR = Path(__file__).resolve().parent
SECREQGEN_ROOT = SCRIPTS_DIR.parent
PROJECT_ROOT = SECREQGEN_ROOT.parent  # coded_project/
TEST_CASES = PROJECT_ROOT / "real_test_cases"

sys.path.insert(0, str(SCRIPTS_DIR))
from asvs_templates import GTRequirement, derive_ground_truth_for_story  # noqa: E402

EXTRACTOR_MODEL = os.environ.get("EXTRACTOR_MODEL", "qwen3:30b-a3b-instruct-2507-q4_K_M")


@dataclass
class UserStory:
    id: str
    title: str
    story: str
    acceptance_criteria: list[str]


# ─────────────────────────────────────────────────────────────────────
# Repo fetching
# ─────────────────────────────────────────────────────────────────────


def normalize_slug(arg: str) -> tuple[str, str]:
    """Accept 'owner/repo' or full GitHub URL → return (owner, repo)."""
    arg = arg.strip().rstrip("/")
    if arg.startswith("http"):
        # https://github.com/owner/repo[.git]
        m = re.search(r"github\.com/([^/]+)/([^/.]+)", arg)
        if not m:
            raise ValueError(f"unrecognized GitHub URL: {arg}")
        return m.group(1), m.group(2)
    parts = arg.split("/")
    if len(parts) != 2:
        raise ValueError(f"expected 'owner/repo', got: {arg}")
    return parts[0], parts[1]


def short_name(owner: str, repo: str) -> str:
    """The slug used as the project directory name. Lowercased, dashed."""
    return repo.lower().replace("_", "-")


def github_raw_fetch(owner: str, repo: str, branch: str, path: str) -> Optional[str]:
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    try:
        out = subprocess.run(
            ["curl", "-fsSL", "-m", "20", url],
            capture_output=True, text=True, check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout
    except Exception:
        return None
    return None


def fetch_repo_docs(owner: str, repo: str) -> dict[str, str]:
    """Return a dict of {path: content} of relevant security/info docs.

    Tries README.md, README.rst, SECURITY.md, docs/SECURITY.md, then
    falls back to package.json / pyproject.toml for system context.
    """
    found: dict[str, str] = {}
    for branch in ("main", "master"):
        for path in (
            "README.md", "README.rst", "Readme.md", "readme.md",
            "SECURITY.md", "docs/SECURITY.md", ".github/SECURITY.md",
            "package.json", "pyproject.toml", "Cargo.toml", "go.mod",
            "Gemfile", "composer.json",
        ):
            if path in found:
                continue
            content = github_raw_fetch(owner, repo, branch, path)
            if content:
                found[path] = content
        if found:  # got something on this branch, don't try the other
            break
    return found


# ─────────────────────────────────────────────────────────────────────
# Ollama-driven user-story extraction
# ─────────────────────────────────────────────────────────────────────


USER_STORY_EXTRACTION_PROMPT = """You are an expert software analyst writing user stories that exercise the SECURITY surface of a software project for a security-requirements evaluation dataset.

Given the README and SECURITY policy below, output 8 user stories that together cover the most security-relevant features. Each story must:
- Have a unique ID like "{prefix}-US-001" through "{prefix}-US-008"
- Have a short title (under 80 chars)
- Use the canonical "As a <role>, I want <action> so <reason>" format for the story body
- Include 3–5 concrete acceptance criteria, each one specific enough to be testable

Cover at least 4 of: registration/login, MFA, password reset, session management, access control / sharing, data encryption, file upload, API access, configuration / deployment.

Output ONLY a JSON object of this exact shape (no markdown, no commentary):
{{
  "user_stories": [
    {{
      "id": "{prefix}-US-001",
      "title": "...",
      "story": "As a ..., I want ... so ...",
      "acceptance_criteria": ["...", "...", "..."]
    }}
  ]
}}

Project: {project_name}

README + Security policy (truncated):
---
{docs_text}
---

Respond now with the JSON object only."""


def truncate(s: str, max_chars: int = 12000) -> str:
    return s if len(s) <= max_chars else s[:max_chars] + "\n\n[...truncated...]"


def call_ollama(prompt: str, model: str = EXTRACTOR_MODEL) -> str:
    """Invoke Ollama via /api/generate; return the model's reply text."""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2},
    })
    out = subprocess.run(
        ["curl", "-fsS", "-m", "600",
         "-X", "POST", "http://localhost:11434/api/generate",
         "-H", "Content-Type: application/json",
         "-d", payload],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout).get("response", "")


def extract_user_stories(project_name: str, prefix: str, docs: dict[str, str]) -> list[UserStory]:
    docs_text = "\n\n".join(
        f"### {p}\n{truncate(c, 6000)}" for p, c in docs.items()
        if p in ("README.md", "Readme.md", "readme.md", "README.rst", "SECURITY.md", "docs/SECURITY.md", ".github/SECURITY.md")
    )
    if not docs_text.strip():
        raise RuntimeError(f"no README/SECURITY docs available for {project_name}")
    prompt = USER_STORY_EXTRACTION_PROMPT.format(
        project_name=project_name, prefix=prefix, docs_text=truncate(docs_text, 14000),
    )
    raw = call_ollama(prompt)
    data = json.loads(raw)
    stories = []
    for s in data.get("user_stories", []):
        stories.append(UserStory(
            id=s.get("id", ""),
            title=s.get("title", ""),
            story=s.get("story", ""),
            acceptance_criteria=s.get("acceptance_criteria", []),
        ))
    return stories


# ─────────────────────────────────────────────────────────────────────
# system_context.yaml builder
# ─────────────────────────────────────────────────────────────────────


def build_system_context(project_name: str, owner: str, repo: str, docs: dict[str, str]) -> str:
    """Auto-built YAML. Uses package files to detect tech stack; falls
    back to plaintext if not enough info.
    """
    stack: list[str] = []
    if "package.json" in docs:
        stack.append("language: JavaScript/TypeScript")
        stack.append("package_manager: npm/yarn/pnpm")
    if "pyproject.toml" in docs:
        stack.append("language: Python")
        stack.append("package_manager: poetry/pip")
    if "Cargo.toml" in docs:
        stack.append("language: Rust")
        stack.append("build_tool: cargo")
    if "go.mod" in docs:
        stack.append("language: Go")
        stack.append("build_tool: go modules")
    if "Gemfile" in docs:
        stack.append("language: Ruby")
        stack.append("package_manager: bundler")
    if "composer.json" in docs:
        stack.append("language: PHP")
        stack.append("package_manager: composer")
    if not stack:
        stack.append("language: unknown")

    # Pull a one-line description from README first paragraph if present.
    readme = (
        docs.get("README.md") or docs.get("Readme.md") or docs.get("readme.md")
        or docs.get("README.rst") or ""
    )
    desc_line = ""
    for line in readme.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("!"):
            desc_line = line[:240]
            break

    yaml = ["project:"]
    yaml.append(f"  name: {project_name}")
    yaml.append(f"  source: https://github.com/{owner}/{repo}")
    yaml.append(f"  description: {desc_line!r}")
    yaml.append("  tech_stack:")
    for s in stack:
        yaml.append(f"    - {s}")
    yaml.append("")
    yaml.append("notes:")
    yaml.append(f"  - Bootstrapped from public GitHub README + SECURITY.md.")
    yaml.append(f"  - Tech stack detected from manifest files; verify before citing.")
    return "\n".join(yaml) + "\n"


# ─────────────────────────────────────────────────────────────────────
# Ground-truth assembly
# ─────────────────────────────────────────────────────────────────────


def build_ground_truth(project_name: str, stories: list[UserStory]) -> dict:
    gt_entries = []
    counter = 0
    for story in stories:
        haystack = " ".join([
            story.title or "",
            story.story or "",
            " ".join(story.acceptance_criteria or []),
        ])
        derived = derive_ground_truth_for_story(haystack)
        story_reqs = []
        for r in derived:
            counter += 1
            story_reqs.append({
                "id": f"GT-{counter:03d}",
                "title": r.title,
                "description": r.description,
                "asvs_category": r.asvs_category,
                "cve_reference": r.asvs_control,
            })
        gt_entries.append({"story_id": story.id, "requirements": story_reqs})
    return {
        "project": project_name,
        "description": f"Auto-bootstrapped ASVS-derived ground truth for {project_name}",
        "sources": ["OWASP ASVS 4.0 (rule-based mapping in scripts/asvs_templates.py)"],
        "ground_truth": gt_entries,
    }


# ─────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────


def bootstrap_one(slug: str, force: bool = False) -> Optional[Path]:
    owner, repo = normalize_slug(slug)
    name = short_name(owner, repo)
    out_dir = TEST_CASES / name
    if out_dir.exists() and not force:
        print(f"  [skip] {name} (exists; use --force to overwrite)")
        return None
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"  [{name}] fetching docs from github.com/{owner}/{repo}…")
    docs = fetch_repo_docs(owner, repo)
    if not docs:
        print(f"  [{name}] no docs fetched — skipping")
        return None
    print(f"  [{name}]   fetched {len(docs)} files: {sorted(docs.keys())}")

    project_name = repo.replace("-", " ").replace("_", " ").title()
    prefix_letters = "".join(w[0] for w in project_name.split() if w)[:3].upper()
    prefix = prefix_letters or repo[:3].upper()

    print(f"  [{name}] extracting user stories via Ollama ({EXTRACTOR_MODEL})…")
    try:
        stories = extract_user_stories(project_name, prefix, docs)
    except Exception as e:
        print(f"  [{name}] user-story extraction failed: {e}")
        return None
    if len(stories) < 4:
        print(f"  [{name}] only {len(stories)} stories produced; suspect a parse issue")

    print(f"  [{name}] writing user_stories.json ({len(stories)} stories)")
    (out_dir / "user_stories.json").write_text(json.dumps({
        "project": project_name,
        "description": f"Auto-extracted user stories for {project_name}",
        "user_stories": [asdict(s) for s in stories],
    }, indent=2))

    print(f"  [{name}] writing system_context.yaml")
    (out_dir / "system_context.yaml").write_text(
        build_system_context(project_name, owner, repo, docs)
    )

    print(f"  [{name}] writing ground_truth.json (rule-based ASVS mapping)")
    gt = build_ground_truth(project_name, stories)
    (out_dir / "ground_truth.json").write_text(json.dumps(gt, indent=2))

    # Save the security docs (if any) for later KB ingestion.
    docs_dir = out_dir / "docs"
    docs_dir.mkdir(exist_ok=True)
    for path, content in docs.items():
        if path.endswith(".md") or path.endswith(".rst"):
            safe = path.replace("/", "__")
            (docs_dir / safe).write_text(content)

    n_reqs = sum(len(e["requirements"]) for e in gt["ground_truth"])
    print(f"  [{name}] ✓ done — {len(stories)} stories, {n_reqs} ground-truth requirements")
    return out_dir


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slugs", nargs="*", help="GitHub repos as 'owner/repo' or full URLs")
    ap.add_argument("--from", dest="from_file", help="File of repo slugs, one per line")
    ap.add_argument("--force", action="store_true", help="Overwrite existing project dirs")
    args = ap.parse_args()

    slugs: list[str] = list(args.slugs)
    if args.from_file:
        for line in Path(args.from_file).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                slugs.append(line)

    if not slugs:
        ap.error("no slugs provided (positional args or --from <file>)")

    print(f"Bootstrapping {len(slugs)} repos into {TEST_CASES}")
    succeeded: list[str] = []
    failed: list[str] = []
    for s in slugs:
        try:
            result = bootstrap_one(s, force=args.force)
            if result:
                succeeded.append(s)
        except Exception as e:
            print(f"  [{s}] FAILED: {e}")
            failed.append(s)

    print()
    print(f"Done.  {len(succeeded)} succeeded, {len(failed)} failed.")
    if failed:
        print("Failed:")
        for s in failed:
            print(f"  - {s}")


if __name__ == "__main__":
    main()
