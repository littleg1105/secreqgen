# Cross-Family Content-Quality Judge — 2026-05-23

LLM-as-judge content-quality scoring for the 20 cells of the cross-family experiment.
Independent of F1 phrasing-matcher.

- Judge: `llama3:8b` (temperature 0.0)
- Scope: each (story, generated_requirement) pair, for both baseline and RAG conditions, from run_001 of each of the 20 cells.
- Total judge calls: 1636
- Verdict scale: `relevant` (directly addresses a story security concern), `partial` (adjacent/vague), `irrelevant` (off-topic).
- Relevance rate = (relevant + 0.5×partial) / total_reqs

## Per-cell content-quality table

| project | model | bl relevance | rg relevance | Δ | per-story d_z |
|---|---|---|---|---|---|
| mastodon | qwen3 | 1.000 | 1.000 | +0.000 | +0.00 |
| mastodon | qwen3.6 | 1.000 | 1.000 | +0.000 | +0.00 |
| mastodon | gemma4 | 1.000 | 0.975 | -0.025 | -0.35 |
| mastodon | mistral-sm | 1.000 | 0.969 | -0.031 | -0.72 **−** |
| keycloak | qwen3 | 1.000 | 0.969 | -0.031 | -0.50 **−** |
| keycloak | qwen3.6 | 0.922 | 1.000 | +0.078 | +0.35 |
| keycloak | gemma4 | 1.000 | 0.950 | -0.050 | -0.47 |
| keycloak | mistral-sm | 0.988 | 0.938 | -0.050 | -0.57 **−** |
| bitwarden | qwen3 | 1.000 | 1.000 | +0.000 | +0.00 |
| bitwarden | qwen3.6 | 0.988 | 1.000 | +0.012 | +0.35 |
| bitwarden | gemma4 | 0.984 | 0.963 | -0.022 | -0.23 |
| bitwarden | mistral-sm | 1.000 | 1.000 | +0.000 | +0.00 |
| gitlab | qwen3 | 1.000 | 0.969 | -0.031 | -0.50 **−** |
| gitlab | qwen3.6 | 1.000 | 0.975 | -0.025 | -0.35 |
| gitlab | gemma4 | 1.000 | 0.950 | -0.050 | -0.35 |
| gitlab | mistral-sm | 1.000 | 0.979 | -0.021 | -0.35 |
| mattermost | qwen3 | 1.000 | 0.979 | -0.021 | -0.54 **−** |
| mattermost | qwen3.6 | 1.000 | 1.000 | +0.000 | +0.00 |
| mattermost | gemma4 | 1.000 | 0.975 | -0.025 | -0.54 **−** |
| mattermost | mistral-sm | 1.000 | 0.990 | -0.010 | -0.35 |

## Aggregate per-model relevance (mean across 5 projects)

| model | mean bl relevance | mean rg relevance | mean Δ |
|---|---|---|---|
| qwen3 | 1.000 | 0.983 | -0.017 |
| qwen3.6 | 0.982 | 0.995 | +0.013 |
| gemma4 | 0.997 | 0.963 | -0.034 |
| mistral-sm | 0.998 | 0.975 | -0.023 |

## Aggregate per-project relevance (mean across 4 models)

| project | mean bl relevance | mean rg relevance | mean Δ |
|---|---|---|---|
| mastodon | 1.000 | 0.986 | -0.014 |
| keycloak | 0.977 | 0.964 | -0.013 |
| bitwarden | 0.993 | 0.991 | -0.002 |
| gitlab | 1.000 | 0.968 | -0.032 |
| mattermost | 1.000 | 0.986 | -0.014 |