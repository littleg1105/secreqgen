"""The YAML text of a system context, and the lines for a review page."""

from __future__ import annotations

import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _SLUG_RE.sub("_", text.lower()).strip("_")


def render_yaml(context: dict) -> str:
    """The YAML text in the form of the project files of the thesis."""
    import yaml  # noqa: PLC0415

    project: dict = {"name": context.get("name", "")}
    if context.get("description"):
        project["description"] = context["description"]
    tech = []
    for item in context.get("tech_stack", []):
        key = _slug(item["kind"]) or "other"
        tech.append({key: item["value"]})
    if tech:
        project["tech_stack"] = tech
    if context.get("security_profile"):
        project["security_profile"] = context["security_profile"]
    out: dict = {"project": project}
    features: dict[str, list[str]] = {}
    for group in context.get("features", []):
        key = _slug(group["group"])
        if key:
            features.setdefault(key, []).extend(i["text"] for i in group["items"])
    if features:
        out["features"] = features
    data = {
        level: [i["text"] for i in items]
        for level, items in context.get("data_sensitivity", {}).items()
        if items
    }
    if data:
        out["data_sensitivity"] = data
    return yaml.safe_dump(
        out,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=1000,
    )


def source_lines(context: dict) -> list[dict]:
    """One entry for each item, for a review page."""
    lines = [
        {
            "where": "tech_stack",
            "text": f"{i['kind']}: {i['value']}",
            "path": i["path"],
        }
        for i in context.get("tech_stack", [])
    ]
    for group in context.get("features", []):
        where = f"features: {group['group']}"
        lines += [
            {"where": where, "text": i["text"], "path": i["path"]}
            for i in group["items"]
        ]
    for level, items in context.get("data_sensitivity", {}).items():
        where = f"data_sensitivity: {level}"
        lines += [
            {"where": where, "text": i["text"], "path": i["path"]} for i in items
        ]
    return lines
