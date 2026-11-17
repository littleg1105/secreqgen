"""Colours, typefaces, sizes and the small stylesheet of the GUI.

The look is light only. The one bold element is the marked quote.
"""

from __future__ import annotations

COLORS = {
    "paper": "#FFFFFF",
    "ink": "#1B1F23",
    "slate": "#5B6570",
    "mist": "#F4F6F8",
    "line": "#D8DEE4",
    "ink_blue": "#1D3A6B",
    "marker": "#FFE082",
    "keep": "#1B7F4B",
    "delete": "#B3261E",
    "caution": "#8A5A00",
}

FONT_TOOL = (
    '"Avenir Next", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
)
FONT_SOURCE = 'Charter, "Iowan Old Style", "Palatino Linotype", Georgia, serif'
FONT_CODE = 'ui-monospace, "SF Mono", Menlo, Consolas, monospace'

SIZES = {"small": 13, "text": 15, "section": 17, "title": 21, "large": 28}

RAIL_WIDTH = 208
CONTENT_MAX_WIDTH = 1180

CSS = f"""
:root {{
  --sr-paper: {COLORS["paper"]};
  --sr-ink: {COLORS["ink"]};
  --sr-slate: {COLORS["slate"]};
  --sr-mist: {COLORS["mist"]};
  --sr-line: {COLORS["line"]};
  --sr-blue: {COLORS["ink_blue"]};
  --sr-marker: {COLORS["marker"]};
  --sr-keep: {COLORS["keep"]};
  --sr-delete: {COLORS["delete"]};
  --sr-caution: {COLORS["caution"]};
  --sr-font-tool: {FONT_TOOL};
  --sr-font-source: {FONT_SOURCE};
  --sr-font-code: {FONT_CODE};
  --sr-size-small: {SIZES["small"]}px;
  --sr-size-text: {SIZES["text"]}px;
  --sr-size-section: {SIZES["section"]}px;
  --sr-size-title: {SIZES["title"]}px;
  --sr-size-large: {SIZES["large"]}px;
  --sr-radius: 4px;
}}
body, .q-body--layout-animate, .q-field, .q-btn {{
  font-family: var(--sr-font-tool);
}}
body {{
  background: var(--sr-paper);
  color: var(--sr-ink);
  font-size: var(--sr-size-text);
  line-height: 1.5;
}}
.nicegui-content {{ padding: 0; gap: 0; }}
.q-btn, .q-field__control, .q-card, .q-dialog__inner > div {{
  border-radius: var(--sr-radius);
  box-shadow: none;
}}
.q-btn {{ text-transform: none; }}
:focus-visible {{
  outline: 3px solid var(--sr-blue);
  outline-offset: 2px;
}}
.sr-rail {{
  background: var(--sr-mist);
  border-right: 1px solid var(--sr-line);
  display: flex;
  flex-direction: column;
  height: 100%;
}}
.sr-rail-name {{
  font-size: var(--sr-size-section);
  font-weight: 600;
  padding: 20px 16px 16px 16px;
}}
.sr-nav-item {{
  padding: 8px 16px;
  min-height: 0;
  color: var(--sr-ink);
  border-left: 3px solid transparent;
  cursor: pointer;
}}
.sr-nav-item:hover {{ background: var(--sr-line); }}
.sr-nav-active {{
  border-left-color: var(--sr-blue);
  background: var(--sr-paper);
  font-weight: 600;
}}
.sr-rail-server {{
  margin-top: auto;
  padding: 12px 16px;
  border-top: 1px solid var(--sr-line);
  font-size: var(--sr-size-small);
  color: var(--sr-slate);
}}
.sr-dot {{
  display: inline-block;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  margin-right: 8px;
  background: var(--sr-slate);
}}
.sr-dot-ok {{ background: var(--sr-keep); }}
.sr-dot-bad {{ background: var(--sr-delete); }}
.sr-main {{
  max-width: {CONTENT_MAX_WIDTH}px;
  margin: 0 auto;
  padding: 32px 32px 64px 32px;
}}
.sr-page-title {{
  font-size: var(--sr-size-title);
  font-weight: 600;
  line-height: 1.3;
}}
.sr-purpose {{ color: var(--sr-slate); }}
.sr-section-title {{
  font-size: var(--sr-size-section);
  font-weight: 600;
}}
.sr-small {{ font-size: var(--sr-size-small); color: var(--sr-slate); }}
.sr-tag {{
  display: inline-block;
  font-size: var(--sr-size-small);
  padding: 1px 8px;
  border: 1px solid var(--sr-line);
  border-radius: var(--sr-radius);
  color: var(--sr-ink);
  background: var(--sr-paper);
}}
.sr-tag-keep {{ color: var(--sr-keep); border-color: var(--sr-keep); }}
.sr-tag-delete {{ color: var(--sr-delete); border-color: var(--sr-delete); }}
.sr-tag-caution {{ color: var(--sr-caution); border-color: var(--sr-caution); }}
.sr-source {{
  font-family: var(--sr-font-source);
  line-height: 1.6;
  max-width: 72ch;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}}
.sr-source > * {{ display: inline; }}
.sr-mark {{
  background: var(--sr-marker);
  border-radius: 2px;
  padding: 0 2px;
}}
.sr-code {{ font-family: var(--sr-font-code); font-size: var(--sr-size-small); }}
.sr-empty {{
  padding: 32px 0;
  color: var(--sr-slate);
}}
.sr-link {{ color: var(--sr-blue); }}
.sr-link-button {{ color: var(--sr-blue); padding: 0; min-height: 0; }}
.sr-steps {{ max-width: 1160px; }}
.sr-steps-bar {{ min-height: 0; margin-bottom: 2px; }}
.sr-steps-full {{ font-size: var(--sr-size-small); }}
.sr-step-words {{ min-height: 132px; }}
.sr-step-title {{
  font-size: var(--sr-size-section);
  font-weight: 600;
  margin-top: 12px;
}}
.sr-step-facts {{ font-weight: 600; margin-top: 2px; }}
.sr-step-text {{ color: var(--sr-slate); max-width: 84ch; margin-top: 2px; }}
.sr-steps-hint {{ display: none; }}
.sr-step-more {{ margin-top: 4px; max-width: 84ch; }}
.sr-step-more .q-item {{
  display: inline-flex;
  padding: 0;
  min-height: 24px;
  color: var(--sr-blue);
  font-size: var(--sr-size-small);
  background: none;
}}
.sr-step-more .q-focus-helper {{ display: none; }}
.sr-step-more .q-item__section--main {{ flex: 0 0 auto; }}
.sr-step-more .q-item__section--side {{ padding-left: 2px; min-width: 0; }}
.sr-step-more .q-expansion-item__content {{ padding: 2px 0 0 0; }}
"""

# The step diagram in the full screen of the browser, for a projector. Each
# browser name of the state needs its own rule block.
_FULL_SCREEN = """
.sr-steps:{state} {{
  background: var(--sr-paper);
  max-width: none;
  padding: 40px 56px;
  justify-content: center;
  overflow: auto;
}}
.sr-steps:{state} svg.srp {{ max-width: none !important; max-height: 62vh; }}
.sr-steps:{state} .sr-steps-bar {{ display: none; }}
.sr-steps:{state} .sr-step-more {{ display: none; }}
.sr-steps:{state} .sr-step-title {{ font-size: 30px; margin-top: 28px; }}
.sr-steps:{state} .sr-step-facts {{ font-size: 22px; margin-top: 6px; }}
.sr-steps:{state} .sr-step-text {{
  font-size: 20px;
  line-height: 1.45;
  max-width: 96ch;
  margin-top: 6px;
}}
.sr-steps:{state} .sr-steps-hint {{
  display: block;
  font-size: var(--sr-size-text);
  color: var(--sr-slate);
  margin-top: 20px;
}}
"""
CSS += "".join(
    _FULL_SCREEN.format(state=state) for state in ("fullscreen", "-webkit-full-screen")
)


def apply_app() -> None:
    """Set the colours of Quasar for the whole app. Call it before ui.run."""
    from nicegui import app

    app.colors(
        primary=COLORS["ink_blue"],
        positive=COLORS["keep"],
        negative=COLORS["delete"],
        warning=COLORS["caution"],
    )


def apply_page() -> None:
    """Add the stylesheet to the page. Call it inside the root function."""
    from nicegui import ui

    ui.add_css(CSS)
