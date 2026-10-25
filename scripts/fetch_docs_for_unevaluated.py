"""Fetch README + SECURITY.md docs for the 6 hand-curated projects
that already have user_stories.json + ground_truth.json +
system_context.yaml but no doc source dir for KB ingestion.

Per-project canonical GitHub repo (curated, not auto-detected — these
are well-known projects):

  discourse  -> discourse/discourse
  gitlab     -> gitlabhq/gitlabhq
  joomla     -> joomla/joomla-cms
  grafana    -> grafana/grafana
  mastodon   -> mastodon/mastodon
  mattermost -> mattermost/mattermost
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
from bootstrap_dataset import TEST_CASES, fetch_repo_docs

PROJECTS = {
    "discourse":  ("discourse",   "discourse"),
    "gitlab":     ("gitlabhq",    "gitlabhq"),
    "joomla":     ("joomla",      "joomla-cms"),
    "grafana":    ("grafana",     "grafana"),
    "mastodon":   ("mastodon",    "mastodon"),
    "mattermost": ("mattermost",  "mattermost"),
}


def main() -> None:
    for proj, (owner, repo) in PROJECTS.items():
        out_dir = TEST_CASES / proj / "docs"
        if out_dir.exists() and any(out_dir.iterdir()):
            print(f"  [skip] {proj}: docs/ already populated")
            continue
        print(f"  [{proj}] fetching from github.com/{owner}/{repo} …", flush=True)
        docs = fetch_repo_docs(owner, repo)
        if not docs:
            print(f"  [{proj}] FAILED — no docs returned")
            continue
        # Filter to actual content files (not manifests)
        kept = {p: c for p, c in docs.items()
                if p.endswith(".md") or p.endswith(".rst")}
        if not kept:
            print(f"  [{proj}] FAILED — only manifests fetched, no markdown docs")
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        for p, c in kept.items():
            safe = p.replace("/", "__")
            (out_dir / safe).write_text(c)
        sizes = sum(len(c) for c in kept.values())
        print(f"  [{proj}] ✓ wrote {len(kept)} files, {sizes:,} chars total → {out_dir}")


if __name__ == "__main__":
    main()
