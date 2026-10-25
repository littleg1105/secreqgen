# Cross-project meta-analysis — v2

Pulled from `results_compare_runs_pilot_v2_kb_fixed/run_comparison_*/multi_run_report.json`

## Per-project per-story paired test

| project | N runs | N stories | mean(rag-baseline) | per-story Wilcoxon p | per-story d_z |
|---|---|---|---|---|---|
| bitwarden | 10 | 8 | +0.0039 | 0.8438 | +0.028 |
| keycloak | 10 | 8 | +0.0406 | 0.6250 | +0.280 |
| nextcloud | 10 | 8 | +0.0113 | 0.7500 | +0.120 |
| strapi | 10 | 7 | -0.0234 | 1.0000 | -0.378 |

## Pooled per-story test (across all projects)

- **N paired stories pooled:** 31
- **Wilcoxon signed-rank statistic:** 45.000
- **p-value (two-sided):** 0.63777
- **Mean diff (rag - baseline):** +0.0091
- **Pooled d_z:** +0.081
- *Paired t-test (sensitivity):* t=0.449, p=0.65649

## Combined p-value (Fisher's method)

- **chi-squared:** 1.855 on 8 df
- **combined p:** 0.98513

Fisher's method combines independent per-project p-values into
a single test of *whether at least one of them is non-null*.
It does not estimate effect size, only significance.

## Project-weighted average d_z

- **N projects:** 4
- **Weighted by stories:** +0.025
- **Unweighted mean across projects:** +0.013

(A full random-effects model would weight by inverse-variance;
for the current N this is a reasonable approximation.)

## Subgroup by KB richness

- **Rich KB (≥200 chunks):** 8 stories pooled, mean diff +0.0406, d_z +0.280
  Wilcoxon p = 0.62500
- **Poor KB (<100 chunks):** 15 stories pooled, mean diff -0.0049, d_z -0.061
  Wilcoxon p = 1.00000
