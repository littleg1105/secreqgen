#!/usr/bin/env python3
"""Threshold sensitivity analysis for SecReqGen evaluations.

This script recalculates metrics at different similarity thresholds
using existing evaluation JSON files to find the optimal cutoff.

Usage:
    python scripts/threshold_analysis.py --results-dir /path/to/real_test_cases
"""

import json
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

# Thresholds to test
THRESHOLDS = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85]


@dataclass
class ThresholdMetrics:
    """Metrics at a specific threshold."""
    threshold: float
    true_positives: int
    false_positives: int
    false_negatives: int
    partial_matches: int
    precision: float
    recall: float
    f1_score: float
    hallucination_rate: float


def recalculate_at_threshold(
    matches: list[dict],
    total_ground_truth: int,
    match_threshold: float,
    partial_threshold: float = 0.50,  # Lower bound for partial
) -> ThresholdMetrics:
    """Recalculate metrics from raw match data at a given threshold."""
    true_positives = 0
    false_positives = 0
    partial_matches = 0
    matched_gt_ids = set()

    for match in matches:
        sim = match.get("similarity_score", 0)
        gt_id = match.get("ground_truth_id")

        if sim >= match_threshold:
            true_positives += 1
            if gt_id:
                matched_gt_ids.add(gt_id)
        elif sim >= partial_threshold:
            partial_matches += 1
            if gt_id:
                matched_gt_ids.add(gt_id)
        else:
            false_positives += 1

    false_negatives = total_ground_truth - len(matched_gt_ids)
    total_generated = len(matches)

    # Calculate metrics
    precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) > 0 else 0.0
    recall = true_positives / (true_positives + false_negatives) if (true_positives + false_negatives) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    hallucination = false_positives / total_generated if total_generated > 0 else 0.0

    return ThresholdMetrics(
        threshold=match_threshold,
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        partial_matches=partial_matches,
        precision=precision,
        recall=recall,
        f1_score=f1,
        hallucination_rate=hallucination,
    )


def analyze_evaluation_file(filepath: Path) -> dict:
    """Analyze a single evaluation JSON file at all thresholds."""
    with open(filepath) as f:
        data = json.load(f)

    results = {
        "file": filepath.name,
        "project": filepath.parent.parent.name,
        "baseline": {},
        "rag": {},
    }

    # Collect all matches from per-story results
    baseline_matches = []
    rag_matches = []
    total_gt = 0

    for story in data.get("per_story_results", []):
        total_gt += story.get("ground_truth_count", 0)
        baseline_matches.extend(story.get("baseline_matches", []))
        rag_matches.extend(story.get("rag_matches", []))

    # Calculate metrics at each threshold
    for threshold in THRESHOLDS:
        baseline_metrics = recalculate_at_threshold(baseline_matches, total_gt, threshold)
        rag_metrics = recalculate_at_threshold(rag_matches, total_gt, threshold)

        results["baseline"][threshold] = {
            "precision": baseline_metrics.precision,
            "recall": baseline_metrics.recall,
            "f1": baseline_metrics.f1_score,
            "hallucination": baseline_metrics.hallucination_rate,
            "tp": baseline_metrics.true_positives,
            "fp": baseline_metrics.false_positives,
            "fn": baseline_metrics.false_negatives,
        }
        results["rag"][threshold] = {
            "precision": rag_metrics.precision,
            "recall": rag_metrics.recall,
            "f1": rag_metrics.f1_score,
            "hallucination": rag_metrics.hallucination_rate,
            "tp": rag_metrics.true_positives,
            "fp": rag_metrics.false_positives,
            "fn": rag_metrics.false_negatives,
        }

    return results


def find_evaluation_files(results_dir: Path) -> list[Path]:
    """Find all evaluation JSON files in the results directory."""
    files = []
    for project_dir in results_dir.iterdir():
        if project_dir.is_dir():
            results_subdir = project_dir / "results"
            if results_subdir.exists():
                for json_file in results_subdir.glob("*.json"):
                    # Skip files with "with_rag" in name (those are KB experiments)
                    if "with_rag" not in json_file.name:
                        files.append(json_file)
    return files


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Threshold sensitivity analysis")
    parser.add_argument(
        "--results-dir",
        type=Path,
        required=True,
        help="Directory containing project evaluation results",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON file for results (optional)",
    )

    args = parser.parse_args()

    if not args.results_dir.exists():
        print(f"Error: Results directory not found: {args.results_dir}")
        sys.exit(1)

    # Find all evaluation files
    eval_files = find_evaluation_files(args.results_dir)

    if not eval_files:
        print("No evaluation files found!")
        sys.exit(1)

    print(f"Found {len(eval_files)} evaluation files\n")

    # Analyze each file
    all_results = []
    for filepath in sorted(eval_files):
        print(f"Analyzing: {filepath.parent.parent.name}/{filepath.name}")
        results = analyze_evaluation_file(filepath)
        all_results.append(results)

    # Print summary table
    print("\n" + "=" * 100)
    print("THRESHOLD SENSITIVITY ANALYSIS")
    print("=" * 100)

    # Header
    print(f"\n{'Project':<15}", end="")
    for t in THRESHOLDS:
        print(f"  {t:.2f}  ", end="")
    print()
    print("-" * 100)

    # Print baseline F1 for each project at each threshold
    print("\nBASELINE F1 Scores:")
    for result in all_results:
        print(f"{result['project']:<15}", end="")
        for t in THRESHOLDS:
            f1 = result['baseline'][t]['f1']
            print(f" {f1*100:5.1f}% ", end="")
        print()

    # Print RAG F1
    print("\nRAG F1 Scores:")
    for result in all_results:
        print(f"{result['project']:<15}", end="")
        for t in THRESHOLDS:
            f1 = result['rag'][t]['f1']
            print(f" {f1*100:5.1f}% ", end="")
        print()

    # Print improvement percentage
    print("\nF1 Improvement (RAG vs Baseline):")
    for result in all_results:
        print(f"{result['project']:<15}", end="")
        for t in THRESHOLDS:
            baseline_f1 = result['baseline'][t]['f1']
            rag_f1 = result['rag'][t]['f1']
            if baseline_f1 > 0:
                improvement = ((rag_f1 - baseline_f1) / baseline_f1) * 100
            elif rag_f1 > 0:
                improvement = 100.0
            else:
                improvement = 0.0
            print(f" {improvement:+5.0f}% ", end="")
        print()

    # Calculate aggregate across all projects
    print("\n" + "=" * 100)
    print("AGGREGATE METRICS (All Projects Combined)")
    print("=" * 100)

    print(f"\n{'Threshold':<10} {'Baseline F1':>12} {'RAG F1':>12} {'Improvement':>12} {'Meets 20%?':>12}")
    print("-" * 60)

    for t in THRESHOLDS:
        baseline_tp = sum(r['baseline'][t]['tp'] for r in all_results)
        baseline_fp = sum(r['baseline'][t]['fp'] for r in all_results)
        baseline_fn = sum(r['baseline'][t]['fn'] for r in all_results)

        rag_tp = sum(r['rag'][t]['tp'] for r in all_results)
        rag_fp = sum(r['rag'][t]['fp'] for r in all_results)
        rag_fn = sum(r['rag'][t]['fn'] for r in all_results)

        # Calculate aggregate metrics
        baseline_prec = baseline_tp / (baseline_tp + baseline_fp) if (baseline_tp + baseline_fp) > 0 else 0
        baseline_rec = baseline_tp / (baseline_tp + baseline_fn) if (baseline_tp + baseline_fn) > 0 else 0
        baseline_f1 = 2 * baseline_prec * baseline_rec / (baseline_prec + baseline_rec) if (baseline_prec + baseline_rec) > 0 else 0

        rag_prec = rag_tp / (rag_tp + rag_fp) if (rag_tp + rag_fp) > 0 else 0
        rag_rec = rag_tp / (rag_tp + rag_fn) if (rag_tp + rag_fn) > 0 else 0
        rag_f1 = 2 * rag_prec * rag_rec / (rag_prec + rag_rec) if (rag_prec + rag_rec) > 0 else 0

        if baseline_f1 > 0:
            improvement = ((rag_f1 - baseline_f1) / baseline_f1) * 100
        elif rag_f1 > 0:
            improvement = 100.0
        else:
            improvement = 0.0

        meets_target = "YES" if improvement >= 20 else "NO"

        print(f"{t:<10.2f} {baseline_f1*100:>11.1f}% {rag_f1*100:>11.1f}% {improvement:>+11.1f}% {meets_target:>12}")

    # Save results if output specified
    if args.output:
        output_data = {
            "thresholds": THRESHOLDS,
            "projects": all_results,
        }
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\nResults saved to: {args.output}")

    print("\n" + "=" * 100)
    print("RECOMMENDATION:")
    print("=" * 100)

    # Find best threshold based on aggregate improvement
    best_threshold = None
    best_improvement = float('-inf')

    for t in THRESHOLDS:
        baseline_tp = sum(r['baseline'][t]['tp'] for r in all_results)
        baseline_fp = sum(r['baseline'][t]['fp'] for r in all_results)
        baseline_fn = sum(r['baseline'][t]['fn'] for r in all_results)

        rag_tp = sum(r['rag'][t]['tp'] for r in all_results)
        rag_fp = sum(r['rag'][t]['fp'] for r in all_results)
        rag_fn = sum(r['rag'][t]['fn'] for r in all_results)

        baseline_prec = baseline_tp / (baseline_tp + baseline_fp) if (baseline_tp + baseline_fp) > 0 else 0
        baseline_rec = baseline_tp / (baseline_tp + baseline_fn) if (baseline_tp + baseline_fn) > 0 else 0
        baseline_f1 = 2 * baseline_prec * baseline_rec / (baseline_prec + baseline_rec) if (baseline_prec + baseline_rec) > 0 else 0

        rag_prec = rag_tp / (rag_tp + rag_fp) if (rag_tp + rag_fp) > 0 else 0
        rag_rec = rag_tp / (rag_tp + rag_fn) if (rag_tp + rag_fn) > 0 else 0
        rag_f1 = 2 * rag_prec * rag_rec / (rag_prec + rag_rec) if (rag_prec + rag_rec) > 0 else 0

        if baseline_f1 > 0:
            improvement = ((rag_f1 - baseline_f1) / baseline_f1) * 100
        elif rag_f1 > 0:
            improvement = 100.0
        else:
            improvement = 0.0

        if improvement > best_improvement:
            best_improvement = improvement
            best_threshold = t

    print(f"\nBest threshold for RAG improvement: {best_threshold:.2f} ({best_improvement:+.1f}% improvement)")
    print("\nNote: Lower thresholds accept more semantic matches, higher thresholds require near-exact matches.")


if __name__ == "__main__":
    main()
