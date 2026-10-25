"""Generate a PowerPoint deck for the SecReqGen thesis status update.

Audience: someone with no prior context — supervisor, examiner, or
anyone joining the project. Each slide builds the story from the
ground up: problem → proposed solution → how to measure → what
went wrong → what works → what we found → what's next.

Output: docs/SecReqGen_Status.pptx

Run from secreqgen/:
    .venv/bin/python scripts/generate_powerpoint.py
"""
from pathlib import Path

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

REPO = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen")
FIG = REPO / "docs" / "figures"
OUT = REPO / "docs" / "SecReqGen_Status.pptx"

# 16:9 widescreen
SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)

# Colors — clean, minimalist
COLOR_BG       = RGBColor(0xFF, 0xFF, 0xFF)
COLOR_TITLE    = RGBColor(0x1A, 0x1A, 0x1A)
COLOR_BODY     = RGBColor(0x33, 0x33, 0x33)
COLOR_ACCENT   = RGBColor(0x1F, 0x77, 0xB4)  # blue
COLOR_GOOD     = RGBColor(0x2C, 0xA0, 0x2C)  # green
COLOR_BAD      = RGBColor(0xD6, 0x27, 0x28)  # red
COLOR_MUTED    = RGBColor(0x88, 0x88, 0x88)
COLOR_RULE     = RGBColor(0xCC, 0xCC, 0xCC)


def set_bg(slide, color):
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = color


def add_text(slide, left, top, width, height, text, *,
             font_size=18, bold=False, color=COLOR_BODY,
             align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, font="Calibri"):
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Inches(0.05)
    tf.margin_top = tf.margin_bottom = Inches(0.05)
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.name = font
    run.font.size = Pt(font_size)
    run.font.bold = bold
    run.font.color.rgb = color
    return tb


def add_paragraphs(slide, left, top, width, height, lines, *,
                   font_size=16, bold_lead=False, color=COLOR_BODY,
                   bullet=False, font="Calibri"):
    """lines: list of strings, each becomes its own paragraph."""
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(0.05)
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = PP_ALIGN.LEFT
        p.space_after = Pt(8)
        run = p.add_run()
        run.text = ("• " + line) if bullet else line
        run.font.name = font
        run.font.size = Pt(font_size)
        run.font.color.rgb = color
        run.font.bold = bold_lead and i == 0
    return tb


def add_rule(slide, left, top, width, color=COLOR_RULE):
    line = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, Emu(12700))
    line.fill.solid()
    line.fill.fore_color.rgb = color
    line.line.fill.background()


def add_picture(slide, path, left, top, width, height=None):
    if height is None:
        return slide.shapes.add_picture(str(path), left, top, width=width)
    return slide.shapes.add_picture(str(path), left, top, width=width, height=height)


def add_title(slide, title, subtitle=None):
    add_text(slide, Inches(0.6), Inches(0.4), Inches(12), Inches(0.8),
             title, font_size=32, bold=True, color=COLOR_TITLE)
    if subtitle:
        add_text(slide, Inches(0.6), Inches(1.2), Inches(12), Inches(0.5),
                 subtitle, font_size=16, color=COLOR_MUTED)
    add_rule(slide, Inches(0.6), Inches(1.65), Inches(12.1), color=COLOR_ACCENT)


def add_footer(slide, slide_num, total):
    add_text(slide, Inches(0.6), Inches(7.05), Inches(8), Inches(0.4),
             "SecReqGen — Thesis Status — G. Georgopoulos — 2026-05-05",
             font_size=10, color=COLOR_MUTED)
    add_text(slide, Inches(11.5), Inches(7.05), Inches(1.3), Inches(0.4),
             f"{slide_num} / {total}",
             font_size=10, color=COLOR_MUTED, align=PP_ALIGN.RIGHT)


# ─────────────────────────────────────────────────────────────────────
# Slide builders
# ─────────────────────────────────────────────────────────────────────


def build():
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    blank_layout = prs.slide_layouts[6]
    slides_total = 22

    # === Slide 1: Title ===========================================
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_text(s, Inches(0.6), Inches(2.6), Inches(12), Inches(1),
             "SecReqGen", font_size=60, bold=True, color=COLOR_ACCENT)
    add_text(s, Inches(0.6), Inches(3.5), Inches(12), Inches(1),
             "RAG-Enhanced Security Requirements Generation",
             font_size=28, bold=True, color=COLOR_TITLE)
    add_text(s, Inches(0.6), Inches(4.1), Inches(12), Inches(0.7),
             "Thesis status — from problem statement to results",
             font_size=20, color=COLOR_MUTED)
    add_rule(s, Inches(0.6), Inches(5.6), Inches(4))
    add_text(s, Inches(0.6), Inches(5.7), Inches(12), Inches(0.4),
             "Student:    G. Georgopoulos",
             font_size=14, color=COLOR_BODY)
    add_text(s, Inches(0.6), Inches(6.05), Inches(12), Inches(0.4),
             "Supervisor: Prof. Panagiotis Kotzanikolaou",
             font_size=14, color=COLOR_BODY)
    add_text(s, Inches(0.6), Inches(6.4), Inches(12), Inches(0.4),
             "Date:       2026-05-05",
             font_size=14, color=COLOR_BODY)

    # === Slide 2: The problem (basics) ============================
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_title(s, "The problem we're trying to solve",
              "Where the thesis comes from")
    add_paragraphs(s, Inches(0.6), Inches(2.0), Inches(12), Inches(5),
                   [
                       "Security engineers in an agile team have to write security requirements for every new feature.",
                       "A 'security requirement' is a sentence like:",
                       "      “Login attempts must be rate-limited and lock the account after 5 failures.”",
                       "These come from analyzing user stories (the feature spec) plus organizational knowledge (policies, past incidents, OWASP standards).",
                       "",
                       "The challenge:",
                       "      •  50–100 user stories per sprint,",
                       "      •  ~30–60 minutes of expert analysis per story,",
                       "      •  security teams cannot keep up with feature velocity.",
                       "",
                       "Result: security gets short-changed, vulnerabilities slip into production.",
                   ], font_size=18)

    # === Slide 3: The proposed solution =========================
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_title(s, "The proposed solution",
              "Use a Large Language Model to draft the requirements")
    add_paragraphs(s, Inches(0.6), Inches(2.0), Inches(12), Inches(5),
                   [
                       "Take a user story → feed to an LLM (Llama / Qwen / GLM …) → out comes a draft security requirement.",
                       "",
                       "Two flavours of doing this:",
                       "      •  Zero-shot — just give the LLM the user story.",
                       "      •  RAG — also retrieve relevant docs (security policies, past architecture notes) and put them in the prompt.",
                       "",
                       "RAG = Retrieval-Augmented Generation. Hypothesis: project-specific docs make the LLM produce better, more specific requirements.",
                       "",
                       "The thesis question:",
                   ], font_size=18)
    add_text(s, Inches(0.9), Inches(6.0), Inches(11), Inches(0.6),
             "Does RAG actually produce better security requirements than the LLM alone?",
             font_size=22, bold=True, color=COLOR_ACCENT)

    # === Slide 4: How do we measure 'better'? =====================
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_title(s, "How do we measure “better”?",
              "F1 against ground-truth security requirements")
    add_paragraphs(s, Inches(0.6), Inches(2.0), Inches(7.0), Inches(4.5),
                   [
                       "For each user story, an expert hand-writes a list of security requirements that SHOULD have been generated. That's the ground truth.",
                       "",
                       "Compare LLM output to ground truth using semantic similarity (sentence-transformers, threshold 0.85).",
                       "",
                       "Standard information-retrieval metrics:",
                       "      •  Precision  =  TP / (TP + FP)",
                       "      •  Recall      =  TP / (TP + FN)",
                       "      •  F1            =  harmonic mean of the two",
                       "",
                       "Ground truth aligned to OWASP ASVS controls + CVE history per project.",
                   ], font_size=16)
    # Sketch
    add_text(s, Inches(8.0), Inches(2.0), Inches(5), Inches(0.4),
             "Test data per project:", font_size=14, bold=True, color=COLOR_TITLE)
    add_paragraphs(s, Inches(8.0), Inches(2.4), Inches(5), Inches(4),
                   [
                       "user_stories.json   (~8 stories)",
                       "ground_truth.json   (CVE-aligned)",
                       "system_context.yaml (architecture)",
                       "docs/   (security docs to ingest into RAG KB)",
                   ], font_size=14, color=COLOR_MUTED, font="Consolas")

    # === Slide 5: First instinct vs reality =======================
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_title(s, "The naive way — and why it doesn't work",
              "Single-run F1 numbers are unreliable")
    add_paragraphs(s, Inches(0.6), Inches(2.0), Inches(12), Inches(4),
                   [
                       "First instinct: run the pipeline once, compute one F1 number per project, report.",
                       "",
                       "Problem: LLMs are non-deterministic. Run the same input twice → different output → different F1.",
                       "",
                       "Earlier in this project I had recorded a “+100% RAG benefit on bitwarden”. That was a single-run number.",
                       "",
                       "When I re-ran it 10 times, the “+100%” disappeared into noise. The original headline was a lucky sample, not a real effect.",
                   ], font_size=18)
    add_text(s, Inches(0.9), Inches(6.0), Inches(11), Inches(1),
             "Conclusion: we need a methodology that distinguishes real signal from sampling noise.",
             font_size=22, bold=True, color=COLOR_ACCENT)

    # === Slide 6: The methodology ===============================
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_title(s, "The methodology — multi-run paired statistics",
              "What I built to make this honest")
    add_paragraphs(s, Inches(0.6), Inches(2.0), Inches(12), Inches(5),
                   [
                       "For each project, run the full pipeline N times under each condition (baseline + RAG), with paired runs.",
                       "",
                       "Compute, for each pair of runs, the difference in F1.",
                       "",
                       "Inferential tests:",
                       "      •  PRIMARY: per-story Wilcoxon signed-rank — “for any given user story, is RAG’s mean F1 reliably higher than baseline’s?”",
                       "      •  SENSITIVITY: per-run Wilcoxon — “for each paired run, is RAG’s aggregate F1 higher?”",
                       "      •  Shapiro-Wilk normality check on the paired differences.",
                       "      •  Effect size: Cohen’s d_z (paired-sample variant, named explicitly).",
                       "",
                       "All grounded in Goodman, Fanelli & Ioannidis (2016) — methods + inferential reproducibility.",
                   ], font_size=15)

    # === Slide 7: How we got here (timeline) =====================
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_title(s, "Two weeks of iteration",
              "Major milestones in this work")
    add_picture(s, FIG / "fig5_journey_timeline.png",
                Inches(0.6), Inches(2.0), width=Inches(12))
    add_paragraphs(s, Inches(0.6), Inches(5.8), Inches(12), Inches(1.5),
                   [
                       "Three high-leverage moments:",
                       "1. The first VETO told me the methodology was wrong (mock LLM in a stochastic-LLM thesis = invalidates the claim).",
                       "2. A validation council told me my fix was almost-right (revised in 3 ways).",
                       "3. The final pilot data revealed the actual structure of RAG benefit on this task.",
                   ], font_size=14)

    # === Slide 8: First findings (broken KB) ===================
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_title(s, "First real evaluation — and a confound",
              "Pilot v1: bitwarden, 10 paired runs")
    add_paragraphs(s, Inches(0.6), Inches(2.0), Inches(12), Inches(2),
                   [
                       "Result: RAG F1 = 0.122, baseline F1 = 0.130. Δ = −0.008. Wilcoxon p = 0.557 (not significant).",
                       "Single-run “+100% on bitwarden” disproved by N=10. Methodology working as intended.",
                   ], font_size=16)
    add_text(s, Inches(0.6), Inches(3.6), Inches(12), Inches(0.4),
             "But on investigation, something specific was wrong:",
             font_size=16, bold=True, color=COLOR_BAD)
    add_paragraphs(s, Inches(0.6), Inches(4.1), Inches(12), Inches(3),
                   [
                       "The vector database had only 23 chunks total — all about strapi/permissions.",
                       "When evaluating bitwarden, retrieval pulled Strapi-specific framing → irrelevant noise.",
                       "Fix: per-project KB. Ingest each project's own docs before evaluating it.",
                   ], font_size=16, bullet=True)

    # === Slide 9: v2 with per-project KB =======================
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_title(s, "Pilot v2 — per-project KB, 4 projects",
              "First clean signal: keycloak")
    add_picture(s, FIG / "fig1_v2_baseline_vs_rag.png",
                Inches(0.6), Inches(2.0), width=Inches(8))
    add_paragraphs(s, Inches(8.8), Inches(2.0), Inches(4.2), Inches(5),
                   [
                       "Keycloak (548 chunks) showed strong significant per-run RAG benefit:",
                       "",
                       "p = 0.002",
                       "d_z = +1.95 (very large)",
                       "10/10 paired runs: RAG > baseline.",
                       "",
                       "First evidence that RAG can produce a real, reproducible improvement.",
                   ], font_size=14)

    # === Slide 10: keycloak trajectories =======================
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_title(s, "Keycloak — what a clean signal looks like",
              "Every paired run, RAG > baseline")
    add_picture(s, FIG / "fig4_keycloak_trajectories.png",
                Inches(1.5), Inches(2.0), width=Inches(10))
    add_text(s, Inches(0.6), Inches(6.5), Inches(12), Inches(0.6),
             "Reproduces in v3 (d_z=+1.07) and again in n20 (d_z=+1.54). Three independent pilots agree.",
             font_size=16, bold=True, color=COLOR_GOOD, align=PP_ALIGN.CENTER)

    # === Slide 11: Trying to scale (v3) ========================
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_title(s, "Trying to scale — auto-bootstrap to 36 projects",
              "Pilot v3: lessons learned, not the result we hoped for")
    add_paragraphs(s, Inches(0.6), Inches(2.0), Inches(12), Inches(2.5),
                   [
                       "Built a pipeline: GitHub repo URL → README + SECURITY.md → LLM-extract user stories → rule-based ASVS ground truth.",
                       "Bootstrapped 26 of 36 repos. Ran the pilot on 12 before pausing.",
                   ], font_size=16, bullet=True)
    add_text(s, Inches(0.6), Inches(4.2), Inches(12), Inches(0.4),
             "Discovery: auto-generated generic ASVS ground truth penalises project-specific RAG output.",
             font_size=16, bold=True, color=COLOR_BAD)
    add_paragraphs(s, Inches(0.6), Inches(4.7), Inches(12), Inches(2.5),
                   [
                       "Most projects produced F1 ≈ 0 in BOTH conditions — the LLM produces project-specific requirements (e.g. ClickHouse-specific access control), but generic ASVS templates don't match that.",
                       "Result: paired tests on 0-vs-0 are degenerate. Path discontinued.",
                       "This negative finding is itself reportable as a methodology contribution.",
                   ], font_size=14, bullet=True)

    # === Slide 12: The pivot (Path A) ===========================
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_title(s, "Pivot: stop scaling the dataset, scale the analysis",
              "Council-validated decision: Path A")
    add_paragraphs(s, Inches(0.6), Inches(2.0), Inches(12), Inches(5),
                   [
                       "I sent the methodology + status documents to a multi-agent council (Claude + Codex + Gemini) for cold review.",
                       "",
                       "All three converged on the same advice:",
                       "      •  STOP building new tools.",
                       "      •  Take the 10 hand-curated projects and run each at N=20 paired runs (instead of N=10).",
                       "      •  Pool the results across all 10 projects in a meta-analysis.",
                       "      •  Reframe the thesis: methodology-first, conditional empirical claim.",
                       "",
                       "I executed this plan. ~12 hours of unattended Ollama compute on a M-series laptop.",
                   ], font_size=16)

    # === Slide 13: N=20 results — the headline =================
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_title(s, "Final results — 10 projects at N=20",
              "Per-run effect size by project")
    add_picture(s, FIG / "fig6_n20_per_run_dz.png",
                Inches(1.0), Inches(2.0), width=Inches(11))
    add_text(s, Inches(0.6), Inches(6.7), Inches(12), Inches(0.6),
             "6 of 10 projects clear statistical significance: 4 RAG-helps, 2 RAG-hurts.",
             font_size=18, bold=True, color=COLOR_TITLE, align=PP_ALIGN.CENTER)

    # === Slide 14: The split ==================================
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_title(s, "The four-vs-two split",
              "RAG benefit is project-dependent, not universal")
    # Two-column table-like layout
    # Left column: helps
    add_text(s, Inches(0.6), Inches(2.0), Inches(6), Inches(0.5),
             "RAG significantly HELPS",
             font_size=20, bold=True, color=COLOR_GOOD)
    helps = [
        "mastodon          d_z = +1.69    p = 0.0002",
        "keycloak          d_z = +1.54    p = 0.0002",
        "gitlab            d_z = +1.10    p = 0.0019",
        "nextcloud         d_z = +0.90    p = 0.0028",
    ]
    add_paragraphs(s, Inches(0.6), Inches(2.6), Inches(6), Inches(3),
                   helps, font_size=15, font="Consolas", color=COLOR_GOOD)
    # Right column: hurts
    add_text(s, Inches(7.0), Inches(2.0), Inches(6), Inches(0.5),
             "RAG significantly HURTS",
             font_size=20, bold=True, color=COLOR_BAD)
    hurts = [
        "mattermost        d_z = −0.99    p = 0.0007",
        "strapi            d_z = −0.71    p = 0.0364",
    ]
    add_paragraphs(s, Inches(7.0), Inches(2.6), Inches(6), Inches(3),
                   hurts, font_size=15, font="Consolas", color=COLOR_BAD)
    # Null
    add_text(s, Inches(0.6), Inches(4.5), Inches(12), Inches(0.5),
             "Null effect (4 projects)",
             font_size=20, bold=True, color=COLOR_MUTED)
    add_text(s, Inches(0.6), Inches(5.0), Inches(12), Inches(0.5),
             "bitwarden, joomla, grafana, discourse — ns, |d_z| < 0.35",
             font_size=15, font="Consolas", color=COLOR_MUTED)
    add_rule(s, Inches(0.6), Inches(5.8), Inches(12.1))
    add_text(s, Inches(0.6), Inches(6.0), Inches(12), Inches(0.5),
             "Pooled per-story (PRIMARY test): p = 0.528, d_z = +0.07",
             font_size=14, color=COLOR_MUTED)
    add_text(s, Inches(0.6), Inches(6.4), Inches(12), Inches(0.5),
             "The pooled null reflects genuine cross-project heterogeneity, not absence of signal.",
             font_size=14, color=COLOR_MUTED)

    # === Slide 15: KB size NOT a predictor ====================
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_title(s, "Surprise: KB size is not the predictor",
              "What I expected vs. what the data shows")
    add_paragraphs(s, Inches(0.6), Inches(2.0), Inches(12), Inches(2),
                   [
                       "v2 hypothesis (after 4 projects): RAG benefit needs a large KB (≥500 chunks).",
                       "n20 evidence (10 projects): hypothesis NOT supported.",
                   ], font_size=16, bullet=True)
    # Examples
    examples = [
        "  mastodon  → 10 chunks       d_z = +1.69 (LARGE positive)",
        "  nextcloud →  9 chunks       d_z = +0.90 (large positive)",
        "  gitlab    → 20 chunks       d_z = +1.10 (large positive)",
        "  bitwarden →133 chunks       d_z = +0.03 (no effect)",
    ]
    add_paragraphs(s, Inches(1.2), Inches(4.2), Inches(12), Inches(3),
                   examples, font_size=14, font="Consolas")
    add_text(s, Inches(0.6), Inches(6.4), Inches(12), Inches(0.5),
             "The deciding factor is project-specific RAG-vs-baseline content alignment, not chunk volume.",
             font_size=16, bold=True, color=COLOR_ACCENT)

    # === Slide 16: Per-story heterogeneity ==================
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_title(s, "Why the pooled per-story test is null",
              "Visualising per-story F1 differences across 10 projects")
    add_picture(s, FIG / "fig7_n20_per_story_diffs.png",
                Inches(1.5), Inches(2.0), width=Inches(10))
    add_text(s, Inches(0.6), Inches(6.5), Inches(12), Inches(0.6),
             "Most stories: tiny diff. A few outliers per project. The averaging-out is real.",
             font_size=16, color=COLOR_BODY, align=PP_ALIGN.CENTER)

    # === Slide 17: The actual thesis claim ====================
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_title(s, "The actual thesis claim",
              "What the evidence supports, in plain English")
    add_text(s, Inches(0.6), Inches(2.0), Inches(12.1), Inches(0.5),
             "What I CAN claim:",
             font_size=18, bold=True, color=COLOR_GOOD)
    add_paragraphs(s, Inches(0.9), Inches(2.5), Inches(12), Inches(2.5),
                   [
                       "The multi-run paired-statistics methodology reliably identifies, at the per-run sensitivity level, where RAG significantly helps versus where it significantly hurts.",
                       "On 10 hand-curated projects at N=20: 4 helps, 2 hurts, 4 null.",
                       "RAG benefit on this task is project-dependent, not universal — and the dependency is not on KB size.",
                   ], font_size=15, bullet=True)
    add_text(s, Inches(0.6), Inches(5.0), Inches(12), Inches(0.5),
             "What I am NOT claiming:",
             font_size=18, bold=True, color=COLOR_BAD)
    add_paragraphs(s, Inches(0.9), Inches(5.5), Inches(12), Inches(1.5),
                   [
                       "“RAG works universally on security requirements generation.” The data does not support this.",
                       "The pooled per-story Wilcoxon is null at p=0.528 — that is reported honestly alongside the per-project results.",
                   ], font_size=15, bullet=True)

    # === Slide 18: What's strongest vs weakest ===============
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_title(s, "Self-assessment",
              "Strongest piece, weakest piece — being honest")
    add_text(s, Inches(0.6), Inches(2.0), Inches(12), Inches(0.5),
             "Strongest: the methodology that catches its own errors.",
             font_size=20, bold=True, color=COLOR_GOOD)
    add_paragraphs(s, Inches(0.9), Inches(2.6), Inches(12), Inches(2),
                   [
                       "Multi-run paired statistics + Wilcoxon + d_z + Shapiro-Wilk + reproducibility framework grounded in cited literature.",
                       "Caught my own “+100%” false positive. Identified a KB-contamination bug. Identified a ground-truth-alignment failure mode.",
                       "Replicates: keycloak gives the same answer across three independent pilots.",
                   ], font_size=14, bullet=True)
    add_text(s, Inches(0.6), Inches(4.5), Inches(12), Inches(0.5),
             "Weakest: the per-story PRIMARY test is underpowered.",
             font_size=20, bold=True, color=COLOR_BAD)
    add_paragraphs(s, Inches(0.9), Inches(5.1), Inches(12), Inches(2),
                   [
                       "With N=8 stories per project, two-sided Wilcoxon p has a floor at ~0.5 — small per-story effects can't clear significance.",
                       "Honestly framed: per-run is the SENSITIVITY test; per-story is what would clear with more stories per project (future work).",
                   ], font_size=14, bullet=True)

    # === Slide 19: What I'd ask the supervisor ================
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_title(s, "What I'd ask in our meeting",
              "Three concrete decisions")
    add_paragraphs(s, Inches(0.6), Inches(2.0), Inches(12), Inches(5),
                   [
                       "1.  Goal #6 wording — send the proposal-amendment pitch this week (Option A, drafted)?",
                       "",
                       "2.  Is the per-project heterogeneity framing acceptable? — “4 helps, 2 hurts, 4 null; methodology reliably exposes the differential.” This is the actually-supported claim.",
                       "",
                       "3.  Future-work framing — does the predictor study (“what predicts whether RAG will help or hurt?”) belong in this thesis's Future Work chapter, or saved for a follow-on paper?",
                   ], font_size=18)
    add_rule(s, Inches(0.6), Inches(5.8), Inches(12.1))
    add_text(s, Inches(0.6), Inches(6.0), Inches(12), Inches(0.4),
             "I'm now ready to write Chapter 7 (Results). All analysis, figures, and reproducibility scripts are in place.",
             font_size=15, bold=True, color=COLOR_ACCENT)

    # === Slide 20: Future work =================================
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_title(s, "Future work — for the thesis or beyond",
              "Sharp questions this work generates")
    add_paragraphs(s, Inches(0.6), Inches(2.0), Inches(12), Inches(5),
                   [
                       "What predicts the helps-vs-hurts split?",
                       "      •  Ground-truth specificity (CVE-aligned vs ASVS-template)?",
                       "      •  Project domain (auth/identity vs. database/storage)?",
                       "      •  Topical alignment between user stories and KB?",
                       "      •  Base-model strength (stronger model = less RAG headroom)?",
                       "",
                       "More stories per project, not more runs.",
                       "      •  N=8 stories floors the per-story Wilcoxon. 20–30 stories per project would change the picture.",
                       "",
                       "CVE-grounded ground truth at scale.",
                       "      •  Pull NVD CVE history per repo → per-CVE expected mitigation as ground truth.",
                       "      •  Council flagged this as risky in this thesis's timeframe. Right project for a follow-on paper.",
                   ], font_size=15)

    # === Slide 21: How to reproduce ==============================
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_title(s, "Reproducing this work",
              "All scripts, all figures, all data")
    add_paragraphs(s, Inches(0.6), Inches(2.0), Inches(12), Inches(5),
                   [
                       "Per-project N=20 paired pilot:",
                       "    bash scripts/run_thesis_n20.sh",
                       "",
                       "Cross-project meta-analysis:",
                       "    PYTHONPATH=src .venv/bin/python scripts/meta_analysis.py --version n20",
                       "",
                       "Regenerate all figures:",
                       "    .venv/bin/python scripts/generate_figures.py",
                       "",
                       "Regenerate this deck:",
                       "    .venv/bin/python scripts/generate_powerpoint.py",
                       "",
                       "All raw results in:",
                       "    real_test_cases/<proj>/results_compare_runs_thesis_n20/",
                   ], font_size=14, font="Consolas")

    # === Slide 22: Thanks =====================================
    s = prs.slides.add_slide(blank_layout)
    set_bg(s, COLOR_BG)
    add_text(s, Inches(0.6), Inches(2.8), Inches(12), Inches(1),
             "Thank you", font_size=60, bold=True, color=COLOR_ACCENT)
    add_text(s, Inches(0.6), Inches(3.9), Inches(12), Inches(1),
             "Questions, pushback, course corrections welcome.",
             font_size=22, color=COLOR_TITLE)
    add_rule(s, Inches(0.6), Inches(5.4), Inches(4))
    add_text(s, Inches(0.6), Inches(5.6), Inches(12), Inches(0.4),
             "Long-form report:    docs/thesis_status_report.md",
             font_size=14, color=COLOR_BODY, font="Consolas")
    add_text(s, Inches(0.6), Inches(5.95), Inches(12), Inches(0.4),
             "Methodology proposals: docs/proposals/",
             font_size=14, color=COLOR_BODY, font="Consolas")
    add_text(s, Inches(0.6), Inches(6.3), Inches(12), Inches(0.4),
             "All scripts:           scripts/",
             font_size=14, color=COLOR_BODY, font="Consolas")

    # ─── Add footers to all slides except the title ────────────
    for i, sl in enumerate(list(prs.slides)[1:], start=2):
        add_footer(sl, i, slides_total)

    prs.save(str(OUT))
    print(f"Wrote {OUT}")
    print(f"Slides: {slides_total}")


if __name__ == "__main__":
    build()
