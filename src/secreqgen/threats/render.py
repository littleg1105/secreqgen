# ruff: noqa: E501
"""The review page of a threat list, as Markdown."""

from __future__ import annotations

from secreqgen.threats.actors import ACTORS


def _row(number: int, row: dict, headers: dict[str, str]) -> list[str]:
    out = [f"**{number}. {row['threat']}**", ""]
    out.append(f"- Kind: {'attack that the documentation describes' if row['type'] == 'attack' else 'breach of a security rule of the documentation'}")
    if row.get("actor"):
        out.append(f"- Threat actor: {ACTORS.get(row['actor'], row['actor'])} (the model's choice, not from the documentation)")
    if row.get("harm"):
        out.append(f"- Harm: {row['harm']}")
    if row.get("story_part"):
        out.append(f"- Part of your story: \"{row['story_part']}\"")
    out.append(f"- Evidence: \"{' '.join(row['quote'].split())}\" ({headers.get(row['source'], row['source'])})")
    out.append(f"- Requirement: {row['requirement']}")
    out.append("- Your verdict: ")
    out.append("")
    return out


def render_markdown(model: dict) -> str:
    """The review page: first the rows whose quote holds a warning word or a rule word, then the other rows."""
    headers = {p["ref"]: p["header"] for p in model["passages"]}
    story = model["story"]
    rows = model["rows"]
    first = [r for r in rows if r.get("signal")]
    second = [r for r in rows if not r.get("signal")]
    out = [f"# Threats for user story {story['id']}: {story['title']}", "", model["draft_notice"],
           "Read the evidence of each row, then write \"keep\" or \"delete\" as your verdict.", "", f"**Story:** {story['story']}", ""]
    number = 0
    out += [f"## Rows from a warning or a rule of the documentation ({len(first)})", ""]
    for kind in ("attack", "rule"):
        for row in first:
            if row["type"] == kind:
                number += 1
                out += _row(number, row, headers)
    if not rows:
        out += ["The documentation passages state no threat and no security rule for this story.", ""]
    elif not first:
        out += ["No row of this kind.", ""]
    if second:
        out += [f"## Rows from other sentences of the documentation ({len(second)})", "",
                "The quote of these rows holds no warning word and no rule word. In the tests such rows were wrong about two times more often. Read them with more care.", ""]
        for kind in ("attack", "rule"):
            for row in second:
                if row["type"] == kind:
                    number += 1
                    out += _row(number, row, headers)
    out.append(f"Passages read: {len(model['passages'])} in {len(model['calls'])} model calls. Model: {model.get('model')}. "
               f"Time: {model['seconds']} s. Rows that the script removed: {len(model['dropped'])}.")
    return "\n".join(out) + "\n"
