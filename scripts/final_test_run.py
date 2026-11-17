"""Final test: the generations. A thin entry point for scripts/xproject_run.py with the builder of the final test.

  --arm main      versions v0_old (OLD) and s1_word_3000 (NEW) on the passages of the old search / the word search
  --arm no_doc    version v3_pn_render_fact_hits with the one fixed line in place of the documentation (NEW-0)
The stories come from real_test_cases/final_test/run_stories.json (arm main: each kind; arm no_doc: the standard
stories). Each other option is an option of scripts/xproject_run.py (--run-name, --versions, --repeats, ...).

Run from coded_project/secreqgen:
  PYTHONPATH=src:scripts .venv/bin/python scripts/final_test_run.py --arm main --project keycloak --run-name ft_a_keycloak \
      --versions v0_old,s1_word_3000 --repeats 3 [--dry-run]
"""

from __future__ import annotations

import argparse
import sys

import xproject_run
from tuning_lib.final_test import ARMS, KINDS, RETRIEVAL_ROOT, FinalTestBuilder, run_ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--arm", required=True, choices=ARMS)
    parser.add_argument("--project", required=True)
    args, rest = parser.parse_known_args(argv)
    builder = FinalTestBuilder((args.project,), arm=args.arm)
    stories = run_ids(args.project, KINDS if args.arm == "main" else ("standard",))
    argv_run = ["--project", args.project, "--stories", ",".join(stories), *rest]
    return xproject_run.main(argv_run, builder=builder, xroot=RETRIEVAL_ROOT)


if __name__ == "__main__":
    sys.exit(main())
