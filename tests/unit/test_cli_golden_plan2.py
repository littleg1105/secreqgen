"""Golden tests of the plan 2 command line: other file types and the context.

The helpers come from test_cli_golden.py. The stand-in clients call no model.
How to record again: set GOLDEN_RECORD=1 and run this file. Then read every
recorded file. The paths in the console text are relative, because the
fixture of the other file moves each test into its own temporary folder.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from . import test_cli_golden as g
from .test_cli_golden import _clean_environment  # noqa: F401  (autouse fixture)

SESSIONS_RST = (
    "Sessions\n========\n\nIdle timeout\n------------\n\n"
    "Set ``session-idle-timeout`` to end a session after a time with no "
    f"activity.\n\n{g.WARNING}\n\nCookies\n-------\n\n{g.RULE}\n"
)
OTHER_PAGE = "# Install\n\nRun the installer with the default options.\n"


class ContextStandIn:
    """A fake model client for the system context. It answers the JSON form."""

    def __init__(self, model_name="stand-in", healthy=True, mode="good", script=None):
        self.model_name = model_name
        self.healthy = healthy
        self.mode = mode
        self.script = script

    def check_health(self):
        return self.healthy

    def generate(self, prompt, system_prompt=None, format=None):
        g.PROMPTS.append(f"[{len(g.PROMPTS) + 1}] {self.model_name} generate")
        if self.script is not None:
            return SimpleNamespace(content=self.script)
        refs = {
            path: ref for ref, path in re.findall(r"(?m)^### \[(F\d+)\] (\S+)", prompt)
        }
        readme, package = refs["README.md"], refs["package.json"]
        tech = [{"kind": "framework", "value": "Express", "source": package}]
        features = [
            {"group": "Orders", "items": [{"text": "Orders", "source": readme}]}
        ]
        description_source = None
        if self.mode == "about_long":
            features = [
                {
                    "group": f"Group {g}",
                    "items": [
                        {
                            "text": f"Feature {g}.{n} with a few more words",
                            "source": readme,
                        }
                        for n in range(6)
                    ],
                }
                for g in range(4)
            ]
            description_source = readme
        if self.mode == "about_one":
            features = [
                {"group": "Orders", "items": [{"text": "Orders", "source": readme}]},
                {"group": "Cart", "items": [{"text": "A cart", "source": package}]},
            ]
            description_source = package
        if self.mode == "dropped":
            tech.append(
                {"kind": "orchestrator", "value": "Kubernetes", "source": package}
            )
            features[0]["items"].append({"text": "Refunds", "source": "F99"})
        return SimpleNamespace(
            content=json.dumps(
                {
                    "name": "Shop",
                    "description": "A web shop for small stores.",
                    "description_source": description_source,
                    "tech_stack": tech,
                    "security_profile": "Medium - holds orders",
                    "features": features,
                    "data_sensitivity": {
                        "critical": [{"text": "Passwords", "source": package}],
                        "high": [],
                        "medium": [],
                    },
                }
            )
        )


def use_context_client(monkeypatch, client):
    def factory(cfg, tested_setup, model_name=None):
        g.log_call("_ollama_client", model_name=model_name, tested_setup=tested_setup)
        return client

    monkeypatch.setattr("secreqgen.cli._ollama_client", factory)
    return client


def small_codebase() -> str:
    Path("shop").mkdir()
    g.write("shop/README.md", "# Shop\nA web shop for small stores.\n")
    g.write("shop/package.json", '{"name": "shop", "dependencies": {"express": "4"}}')
    return "shop"


CONTEXT = ["generate", "context"]


def test_context_ok_output(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn())
    folder = small_codebase()
    g.run_case(
        "context_ok_output",
        [*CONTEXT, folder, "--output", "out/shop.yaml", "--product", "Shop"],
        files=("out/shop.yaml",),
    )


def test_context_ok_console(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn())
    folder = small_codebase()
    g.run_case("context_ok_console", [*CONTEXT, folder])


def test_context_dropped(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn(mode="dropped"))
    folder = small_codebase()
    g.run_case("context_dropped", [*CONTEXT, folder])


def test_context_no_folder(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn())
    g.run_case("context_no_folder", [*CONTEXT, "nofolder"])


def test_context_nothing_useful(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn())
    Path("empty").mkdir()
    g.write("empty/notes.bin", "x")
    g.run_case("context_nothing_useful", [*CONTEXT, "empty"])


def test_context_no_server(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn(healthy=False))
    folder = small_codebase()
    g.run_case("context_no_server", [*CONTEXT, folder])


def test_context_bad_answers(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn(script="not json at all"))
    folder = small_codebase()
    g.run_case("context_bad_answers", [*CONTEXT, folder])


def test_context_output_exists(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn())
    folder = small_codebase()
    Path("out").mkdir()
    g.write("out/shop.yaml", "MY HAND EDITED CONTEXT\n")
    g.run_case(
        "context_output_exists",
        [*CONTEXT, folder, "--output", "out/shop.yaml"],
        files=("out/shop.yaml",),
    )


def test_context_output_below_a_file(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn())
    folder = small_codebase()
    g.run_case(
        "context_output_below_a_file",
        [*CONTEXT, folder, "--output", "shop/README.md/x.yaml"],
    )


def test_context_yaml_on_a_narrow_console_is_valid(monkeypatch):
    """A long line of the YAML text stays one line on the console."""
    import secreqgen.cli
    import yaml

    long_text = "A web shop for small stores that sells goods to customers " * 3
    answer = {
        "name": "Shop",
        "description": long_text.strip(),
        "tech_stack": [{"kind": "framework", "value": "Express", "source": "F3"}],
        "security_profile": "Medium - holds orders",
        "features": [
            {"group": "Orders", "items": [{"text": "Orders", "source": "F2"}]}
        ],
        "data_sensitivity": {"critical": [], "high": [], "medium": []},
    }
    use_context_client(monkeypatch, ContextStandIn(script=json.dumps(answer)))
    monkeypatch.setattr(secreqgen.cli.console, "_width", 60)
    folder = small_codebase()
    lines = "\n".join(g.describe([*CONTEXT, folder])).splitlines()
    start = lines.index("project:")
    end = next(i for i in range(start, len(lines)) if lines[i].startswith("DRAFT"))
    loaded = yaml.safe_load("\n".join(lines[start:end]))
    assert loaded["project"]["description"] == long_text.strip()


def mixed_docs() -> str:
    Path("docs").mkdir()
    g.write("docs/install.md", OTHER_PAGE)
    g.write("docs/sessions.rst", SESSIONS_RST)
    return "docs"


def test_threats_other_formats_mixed(monkeypatch):
    g.use_threat_clients(monkeypatch)
    g.threat_stories_file()
    mixed_docs()
    g.run_case(
        "threats_other_formats_mixed",
        g.threat_args("--other-formats", "--no-check", "--output", "out"),
        files=g.THREAT_FILES,
    )


def test_threats_other_formats_md_only(monkeypatch):
    g.use_threat_clients(monkeypatch)
    g.threat_stories_file()
    g.docs_folder()
    g.run_case(
        "threats_other_formats_md_only",
        g.threat_args("--other-formats", "--output", "out"),
        files=g.THREAT_FILES,
    )
    new = (g.GOLDEN_DIR / "threats_other_formats_md_only.txt").read_text("utf-8")
    old = (g.GOLDEN_DIR / "threats_default_pair.txt").read_text("utf-8")
    assert new == old


def test_requirements_other_formats_mixed(monkeypatch):
    g.use_client(monkeypatch, g.StandInClient())
    g.stories_file()
    mixed_docs()
    g.run_case(
        "requirements_other_formats_mixed",
        [
            "generate",
            "requirements",
            "stories.json",
            "--docs",
            "docs",
            "--other-formats",
        ],
    )


def test_requirements_other_formats_no_docs(monkeypatch):
    g.use_client(monkeypatch, g.StandInClient())
    g.stories_file()
    g.run_case(
        "requirements_other_formats_no_docs",
        ["generate", "requirements", "stories.json", "--other-formats"],
    )


# the paragraph "About the product" ---------------------------------------------

ABOUT = ["generate", "about"]
CONTEXT_FILE = (
    "project:\n  name: Shop\n  description: A web shop for small stores.\n"
    "features:\n  orders:\n    - Orders and payments\n    - Refunds.\n"
    "  admin_tools:\n    - Stock lists\n"
)


def context_file() -> str:
    return g.write("shop.yaml", CONTEXT_FILE)


def test_about_ok_console(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn())
    folder = small_codebase()
    g.run_case("about_ok_console", [*ABOUT, folder])


def test_about_ok_output(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn(mode="about_one"))
    folder = small_codebase()
    g.run_case(
        "about_ok_output",
        [*ABOUT, folder, "--output", "out/about.txt", "--product", "Shop Plus"],
        files=("out/about.txt",),
    )


def test_about_features_left_out(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn(mode="about_long"))
    folder = small_codebase()
    g.run_case("about_features_left_out", [*ABOUT, folder, "-m", "other-model"])


def test_about_dropped_lines_are_not_in_the_paragraph(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn(mode="dropped"))
    folder = small_codebase()
    g.run_case("about_dropped", [*ABOUT, folder])


def test_about_file_console(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn())
    g.run_case("about_file_console", [*ABOUT, context_file()])
    assert not g.CALLS  # no model client was made


def test_about_file_output(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn())
    g.run_case(
        "about_file_output",
        [*ABOUT, context_file(), "-o", "about.txt", "--product", "Shop Two"],
        files=("about.txt",),
    )


def test_about_file_with_no_features(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn())
    g.write("keycloak.yaml", "system:\n  name: Keycloak\n  description: An IAM tool.\n")
    g.run_case("about_file_no_features", [*ABOUT, "keycloak.yaml"])


def test_about_file_with_no_name(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn())
    g.write("noname.yaml", "features:\n  a:\n    - Orders\n")
    g.run_case("about_file_no_name", [*ABOUT, "noname.yaml"])


def test_about_file_is_no_yaml_mapping(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn())
    g.write("list.yaml", "- a\n- b\n")
    g.run_case("about_file_no_mapping", [*ABOUT, "list.yaml"])


def test_about_model_with_a_file(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn())
    g.run_case("about_model_with_file", [*ABOUT, context_file(), "--model", "m:1"])


def test_about_output_exists(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn())
    folder = small_codebase()
    Path("out").mkdir()
    g.write("out/about.txt", "MY HAND EDITED PARAGRAPH\n")
    g.run_case(
        "about_output_exists",
        [*ABOUT, folder, "--output", "out/about.txt"],
        files=("out/about.txt",),
    )


class LateStandIn(ContextStandIn):
    """It makes the output file while the model works."""

    def generate(self, prompt, system_prompt=None, format=None):
        Path("late.txt").write_text("MY OWN FILE\n", encoding="utf-8")
        return super().generate(prompt, system_prompt, format)


def test_about_output_file_that_appears_during_the_run_is_kept(monkeypatch):
    use_context_client(monkeypatch, LateStandIn())
    folder = small_codebase()
    lines = g.describe([*ABOUT, folder, "--output", "late.txt"])
    assert lines[0] == "exit=1"
    assert "Error: The output file exists: " in "\n".join(lines)
    assert Path("late.txt").read_text(encoding="utf-8") == "MY OWN FILE\n"


def test_about_source_not_there(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn())
    g.run_case("about_no_source", [*ABOUT, "nofolder"])


def test_about_no_server(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn(healthy=False))
    folder = small_codebase()
    g.run_case("about_no_server", [*ABOUT, folder])


def test_about_nothing_useful(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn())
    Path("empty").mkdir()
    g.write("empty/notes.bin", "x")
    g.run_case("about_nothing_useful", [*ABOUT, "empty"])


def test_about_bad_answers(monkeypatch):
    use_context_client(monkeypatch, ContextStandIn(script="not json at all"))
    folder = small_codebase()
    g.run_case("about_bad_answers", [*ABOUT, folder])


def test_the_paragraph_that_the_command_writes_goes_into_generate_threats(
    monkeypatch,
):
    use_context_client(monkeypatch, ContextStandIn(mode="about_long"))
    folder = small_codebase()
    lines = g.describe([*ABOUT, folder, "--output", "about.txt"])
    assert lines[0] == "exit=0"
    written = Path("about.txt").read_text(encoding="utf-8")
    assert written.endswith(".\n") and len(written.split()) <= 120
    g.use_threat_clients(monkeypatch)
    g.threat_stories_file()
    g.docs_folder()
    g.CALLS.clear()
    g.PROMPTS.clear()
    lines = g.describe(
        g.threat_args("--no-check", "--about-file", "about.txt", "--output", "out")
    )
    assert lines[0] == "exit=0", lines
    saved = json.loads(Path("out/US-1/threats.json").read_text(encoding="utf-8"))
    assert saved["about"] == " ".join(written.split())


# the options --story-id, --prompts-dir and --model ------------------------------
# Each case has a golden file. The cases that must stop before any model call
# also assert that no client was made.

IDS3 = ("US-1", "US-2", "US-3")
THREATS = ["generate", "threats", "stories.json", "--docs", "docs"]
REQUIREMENTS = ["generate", "requirements", "stories.json", "--docs", "docs"]


def three_stories() -> None:
    g.threat_stories_file(IDS3)
    g.docs_folder()


def use_requirements_client(monkeypatch):
    """The command passes model_name only with --model. The log shows it."""
    made = []

    def factory(cfg, tested_setup, model_name=None):
        g.log_call("_ollama_client", model_name=model_name, tested_setup=tested_setup)
        client = g.StandInClient(model_name or cfg.llm.model_name)
        made.append(client)
        return client

    monkeypatch.setattr("secreqgen.cli._ollama_client", factory)
    return made


def prompt_folder(kind: str, missing: str | None = None) -> str:
    """Copy the tested prompt files to sets/<kind>/mine and change each text.

    missing: the name of one file to remove. Return the folder as a text.
    """
    from secreqgen.application.prompt_sets import FILE_NAMES, copy_prompt_set

    folder = copy_prompt_set(kind, "mine", Path("sets"))
    for filename in FILE_NAMES[kind]:
        path = folder / filename
        marker = "MARKER-" + filename.removesuffix(".txt")
        path.write_text(
            path.read_text(encoding="utf-8").rstrip("\n") + "\n" + marker + "\n",
            encoding="utf-8",
        )
    if missing is not None:
        (folder / missing).unlink()
    return folder.as_posix()


def test_threats_two_stories_of_three(monkeypatch):
    g.use_threat_clients(monkeypatch)
    three_stories()
    g.run_case(
        "threats_two_stories_of_three",
        [
            *THREATS,
            "--story-id",
            "US-3",
            "--story-id",
            "US-1",
            "--no-check",
            "--output",
            "out",
        ],
        files=("out/US-1/threats.json", "out/US-3/threats.json"),
    )
    assert not Path("out/US-2").exists()
    assert sorted(p.name for p in Path("out").iterdir()) == ["US-1", "US-3"]


def test_threats_story_id_not_in_the_file(monkeypatch):
    g.use_threat_clients(monkeypatch)
    three_stories()
    g.run_case(
        "threats_story_id_not_in_the_file",
        [*THREATS, "--story-id", "US-1", "--story-id", "NOPE", "--output", "out"],
    )
    assert not g.CALLS  # no model client was made
    assert not Path("out").exists()


def test_threats_same_story_id_two_times(monkeypatch):
    g.use_threat_clients(monkeypatch)
    three_stories()
    g.run_case(
        "threats_same_story_id_two_times",
        [*THREATS, "--story-id", "US-1", "--story-id", "US-1", "--output", "out"],
        files=("out/US-1/threats.json",),
    )
    assert sorted(p.name for p in Path("out").iterdir()) == ["US-1"]
    # The screen text is the text of one --story-id.
    g.CALLS.clear()
    g.PROMPTS.clear()
    once = g.describe([*THREATS, "--story-id", "US-1", "--output", "out2"])
    twice = g.describe(
        [*THREATS, "--story-id", "US-1", "--story-id", "US-1", "--output", "out3"]
    )
    assert once == twice


def test_threats_prompts_dir(monkeypatch):
    g.use_threat_clients(monkeypatch)
    g.threat_stories_file()
    g.docs_folder()
    folder = prompt_folder("threats")
    g.run_case(
        "threats_prompts_dir",
        [*THREATS, "--prompts-dir", folder, "--output", "out"],
        files=g.THREAT_FILES,
    )
    saved = json.loads(Path("out/US-1/threats.json").read_text(encoding="utf-8"))
    assert saved["prompt_set"] == "user:mine"
    assert saved["tested_prompts"] is False


def test_threats_prompts_dir_not_there(monkeypatch):
    g.use_threat_clients(monkeypatch)
    g.threat_stories_file()
    g.docs_folder()
    g.run_case(
        "threats_prompts_dir_not_there",
        [*THREATS, "--prompts-dir", "sets/nothing", "--output", "out"],
    )
    assert not g.CALLS
    assert not Path("out").exists()


def test_threats_prompts_dir_with_a_file_missing(monkeypatch):
    g.use_threat_clients(monkeypatch)
    g.threat_stories_file()
    g.docs_folder()
    folder = prompt_folder("threats", missing="labels_prompt.txt")
    g.run_case(
        "threats_prompts_dir_file_missing",
        [*THREATS, "--prompts-dir", folder, "--output", "out"],
    )
    assert not g.CALLS
    assert not Path("out").exists()


def test_requirements_two_stories_of_three(monkeypatch):
    use_requirements_client(monkeypatch)
    three_stories()
    g.run_case(
        "requirements_two_stories_of_three",
        [*REQUIREMENTS, "--story-id", "US-3", "--story-id", "US-1"],
    )
    assert len(g.PROMPTS) == 2


def test_requirements_story_id_not_in_the_file(monkeypatch):
    use_requirements_client(monkeypatch)
    three_stories()
    g.run_case(
        "requirements_story_id_not_in_the_file",
        [*REQUIREMENTS, "--story-id", "US-2", "--story-id", "NOPE"],
    )
    assert not g.CALLS
    assert not g.PROMPTS


def test_requirements_same_story_id_two_times(monkeypatch):
    use_requirements_client(monkeypatch)
    three_stories()
    g.run_case(
        "requirements_same_story_id_two_times",
        [*REQUIREMENTS, "--story-id", "US-2", "--story-id", "US-2"],
    )
    assert len(g.PROMPTS) == 1


def test_requirements_prompts_dir(monkeypatch):
    use_requirements_client(monkeypatch)
    g.threat_stories_file()
    g.docs_folder()
    folder = prompt_folder("requirements")
    g.run_case("requirements_prompts_dir", [*REQUIREMENTS, "--prompts-dir", folder])
    joined = "\n".join(g.PROMPTS)
    assert "MARKER-system_prompt" in joined
    assert "MARKER-rag_user_prompt" in joined


def test_requirements_prompts_dir_not_there(monkeypatch):
    use_requirements_client(monkeypatch)
    g.threat_stories_file()
    g.docs_folder()
    g.run_case(
        "requirements_prompts_dir_not_there",
        [*REQUIREMENTS, "--prompts-dir", "sets/nothing"],
    )
    assert not g.CALLS


def test_requirements_prompts_dir_with_a_file_missing(monkeypatch):
    use_requirements_client(monkeypatch)
    g.threat_stories_file()
    g.docs_folder()
    folder = prompt_folder("requirements", missing="rag_user_prompt.txt")
    g.run_case(
        "requirements_prompts_dir_file_missing",
        [*REQUIREMENTS, "--prompts-dir", folder],
    )
    assert not g.CALLS


def test_requirements_model(monkeypatch):
    use_requirements_client(monkeypatch)
    g.threat_stories_file()
    g.docs_folder()
    g.run_case("requirements_model", [*REQUIREMENTS, "--model", " other-model:7b "])
    assert all("other-model:7b generate" in text for text in g.PROMPTS)


def test_requirements_model_short_option(monkeypatch):
    use_requirements_client(monkeypatch)
    g.threat_stories_file()
    g.docs_folder()
    lines = g.describe([*REQUIREMENTS, "-m", "short:1"])
    assert lines[0] == "exit=0", lines
    assert "model_name='short:1'" in "\n".join(g.CALLS)


def test_requirements_empty_model(monkeypatch):
    use_requirements_client(monkeypatch)
    g.threat_stories_file()
    g.docs_folder()
    g.run_case("requirements_empty_model", [*REQUIREMENTS, "--model", "  "])
    assert not g.CALLS
