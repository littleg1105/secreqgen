# Cross-model comparison — qwen3.6 sub-test

**Question:** does the per-project RAG-helps-vs-hurts pattern observed under `qwen3:30b-a3b-instruct-2507-q4_K_M` (the baseline thesis model, N=20) generalise to a different base model — `qwen3.6:latest` (newer generation, A3B MoE, 24GB, think:false, N=10)?

## Per-project: same-condition F1 across models

| project | model | baseline F1 | RAG F1 | Δ F1 (RAG-baseline) | per-run d_z | per-run p |
|---|---|---|---|---|---|---|
| mastodon | **qwen3** | 0.022 ± 0.020 | 0.074 ± 0.021 | +0.0514 | +1.693 | 0.0002 |
| mastodon | **qwen3.6** | 0.061 ± 0.021 | 0.076 ± 0.048 | +0.0145 | +0.300 | 0.3750 |
| mattermost | **qwen3** | 0.075 ± 0.032 | 0.045 ± 0.026 | -0.0303 | -0.995 | 0.0007 |
| mattermost | **qwen3.6** | 0.020 ± 0.028 | 0.025 ± 0.022 | +0.0047 | +0.161 | 0.6875 |
| bitwarden | **qwen3** | 0.123 ± 0.017 | 0.124 ± 0.031 | +0.0012 | +0.029 | 0.9702 |
| bitwarden | **qwen3.6** | 0.066 ± 0.034 | 0.100 ± 0.044 | +0.0337 | +0.634 | 0.1309 |
| keycloak | **qwen3** | 0.057 ± 0.038 | 0.114 ± 0.040 | +0.0576 | +1.544 | 0.0002 |
| keycloak | **qwen3.6** | 0.046 ± 0.029 | 0.045 ± 0.022 | -0.0005 | -0.012 | 0.9219 |
| gitlab | **qwen3** | 0.037 ± 0.024 | 0.083 ± 0.028 | +0.0457 | +1.105 | 0.0019 |
| gitlab | **qwen3.6** | 0.014 ± 0.023 | 0.057 ± 0.030 | +0.0430 | +1.165 | 0.0371 |
| nextcloud | **qwen3** | 0.039 ± 0.017 | 0.065 ± 0.028 | +0.0261 | +0.900 | 0.0028 |
| nextcloud | **qwen3.6** | 0.046 ± 0.017 | 0.068 ± 0.019 | +0.0220 | +1.281 | 0.0020 |
| strapi | **qwen3** | 0.043 ± 0.022 | 0.023 ± 0.019 | -0.0206 | -0.706 | 0.0364 |
| strapi | **qwen3.6** | 0.012 ± 0.019 | 0.015 ± 0.027 | +0.0038 | +0.134 | 0.8750 |
| discourse | **qwen3** | 0.024 ± 0.018 | 0.024 ± 0.018 | +0.0002 | +0.011 | 0.8159 |
| discourse | **qwen3.6** | 0.101 ± 0.017 | 0.077 ± 0.034 | -0.0241 | -0.543 | 0.1602 |
| joomla | **qwen3** | 0.156 ± 0.037 | 0.144 ± 0.016 | -0.0119 | -0.289 | 0.2305 |
| joomla | **qwen3.6** | 0.105 ± 0.024 | 0.096 ± 0.018 | -0.0085 | -0.299 | 0.4922 |
| grafana | **qwen3** | 0.084 ± 0.023 | 0.079 ± 0.028 | -0.0048 | -0.120 | 0.6813 |
| grafana | **qwen3.6** | 0.085 ± 0.023 | 0.112 ± 0.059 | +0.0269 | +0.447 | 0.2754 |

## Direction-of-effect comparison

Did the qwen3.6 sub-test reach the **same per-project verdict** as the qwen3 baseline?

| project | qwen3 verdict | qwen3.6 verdict | agree? |
|---|---|---|---|
| mastodon | STRONG + | null | **✗** |
| mattermost | STRONG − | null | **✗** |
| bitwarden | null | null | **✓** |
| keycloak | STRONG + | null | **✗** |
| gitlab | STRONG + | STRONG + | **✓** |
| nextcloud | STRONG + | STRONG + | **✓** |
| strapi | STRONG − | null | **✗** |
| discourse | null | null | **✓** |
| joomla | null | null | **✓** |
| grafana | null | null | **✓** |

## Baseline-only F1 across models

Tests the hypothesis that a stronger base model produces stronger zero-shot output, leaving less RAG headroom.

| project | baseline F1 (qwen3) | baseline F1 (qwen3.6) | Δ baseline (q3.6 - q3) |
|---|---|---|---|
| mastodon | 0.022 | 0.061 | +0.0391 |
| mattermost | 0.075 | 0.020 | -0.0547 |
| bitwarden | 0.123 | 0.066 | -0.0568 |
| keycloak | 0.057 | 0.046 | -0.0110 |
| gitlab | 0.037 | 0.014 | -0.0230 |
| nextcloud | 0.039 | 0.046 | +0.0072 |
| strapi | 0.043 | 0.012 | -0.0317 |
| discourse | 0.024 | 0.101 | +0.0775 |
| joomla | 0.156 | 0.105 | -0.0516 |
| grafana | 0.084 | 0.085 | +0.0004 |
