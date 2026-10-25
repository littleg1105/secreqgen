# Cross-project meta-analysis — n20

Pulled from `results_compare_runs_thesis_n20/run_comparison_*/multi_run_report.json`

## Per-project per-story paired test

| project | N runs | N stories | mean(rag-baseline) | per-story Wilcoxon p | per-story d_z |
|---|---|---|---|---|---|
| bitwarden | 20 | 8 | +0.0010 | 1.0000 | +0.007 |
| discourse | 20 | 8 | +0.0043 | 0.5000 | +0.531 |
| gitlab | 20 | 8 | +0.0300 | 0.8750 | +0.222 |
| grafana | 20 | 8 | +0.0066 | 1.0000 | +0.058 |
| joomla | 20 | 8 | -0.0354 | 0.6250 | -0.320 |
| keycloak | 20 | 8 | +0.0527 | 0.6250 | +0.346 |
| mastodon | 20 | 8 | +0.0500 | 0.5000 | +0.483 |
| mattermost | 20 | 8 | -0.0410 | 0.6250 | -0.308 |
| nextcloud | 20 | 8 | +0.0212 | 0.3750 | +0.270 |
| strapi | 20 | 7 | -0.0218 | 0.5000 | -0.514 |

## Pooled per-story test (across all projects)

- **N paired stories pooled:** 79
- **Wilcoxon signed-rank statistic:** 401.000
- **p-value (two-sided):** 0.52775
- **Mean diff (rag - baseline):** +0.0071
- **Pooled d_z:** +0.066
- *Paired t-test (sensitivity):* t=0.582, p=0.56208

## Combined p-value (Fisher's method)

- **chi-squared:** 9.208 on 20 df
- **combined p:** 0.98038

Fisher's method combines independent per-project p-values into
a single test of *whether at least one of them is non-null*.
It does not estimate effect size, only significance.

## Project-weighted average d_z

- **N projects:** 10
- **Weighted by stories:** +0.085
- **Unweighted mean across projects:** +0.077

(A full random-effects model would weight by inverse-variance;
for the current N this is a reasonable approximation.)

## Subgroup by KB richness

- **Rich KB (≥200 chunks):** 8 stories pooled, mean diff +0.0527, d_z +0.346
  Wilcoxon p = 0.62500
- **Poor KB (<100 chunks):** 15 stories pooled, mean diff +0.0012, d_z +0.018
  Wilcoxon p = 0.75315
