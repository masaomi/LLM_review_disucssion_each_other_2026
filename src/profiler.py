"""
Performance Profiler.

Generates comprehensive performance profiles for each model by aggregating
cross-evaluation scores, applying bias corrections, and performing
disagreement analysis.

Includes anomaly detection to filter corrupted evaluation data (e.g.,
partial JSON repairs that produce implausible score patterns).
"""

from collections import defaultdict
from typing import Any

import numpy as np
from pydantic import BaseModel
from rich.console import Console

from .evaluator import CrossEvaluation
from .bias_detector import BiasReport, _avg_score

console = Console()


class DomainProfile(BaseModel):
    """Performance scores for a single domain."""

    domain: str
    raw_scores: dict[str, float] = {}  # criterion -> raw avg
    corrected_scores: dict[str, float] = {}  # criterion -> bias-corrected
    weighted_score: float = 0.0
    sample_count: int = 0


class ModelProfile(BaseModel):
    """Complete performance profile for a single model."""

    model_key: str
    display_name: str = ""
    overall_score: float = 0.0
    domain_profiles: dict[str, DomainProfile] = {}
    strengths: list[str] = []
    weaknesses: list[str] = []
    evaluator_reliability: float = 0.0  # How reliable as evaluator
    bias_summary: dict[str, float] = {}


class DisagreementCase(BaseModel):
    """A case where evaluators significantly disagreed."""

    task_id: str
    evaluated_key: str
    evaluator_scores: dict[str, float] = {}  # evaluator -> avg score
    score_range: float = 0.0
    std_deviation: float = 0.0
    possible_reasons: list[str] = []


class PerformanceReport(BaseModel):
    """Complete performance analysis report."""

    model_profiles: dict[str, ModelProfile] = {}
    rankings: dict[str, list[str]] = {}  # domain -> [model_key sorted]
    disagreements: list[DisagreementCase] = []
    insights: list[str] = []


CRITERIA = ["accuracy", "completeness", "logical_consistency",
            "clarity", "originality"]

# Default weights per criteria
DEFAULT_WEIGHTS = {
    "accuracy": 0.25,
    "completeness": 0.20,
    "logical_consistency": 0.25,
    "clarity": 0.15,
    "originality": 0.15,
}


def build_profiles(
    evaluations: dict[str, list[CrossEvaluation]],
    bias_report: BiasReport,
    task_domains: dict[str, str],
    model_display_names: dict[str, str] | None = None,
) -> PerformanceReport:
    """
    Build comprehensive performance profiles from evaluation data.

    Args:
        evaluations: {task_id: [CrossEvaluation]}
        bias_report: BiasReport from bias_detector
        task_domains: {task_id: domain_name}
        model_display_names: {model_key: display_name}

    Returns:
        PerformanceReport with profiles, rankings, and insights
    """
    model_display_names = model_display_names or {}

    # Flatten evaluations (exclude self-bias tests, errors, and anomalies)
    valid_evals = []
    anomaly_count = 0
    for evals in evaluations.values():
        for e in evals:
            if e.error or e.is_self_bias_test:
                continue
            if _is_anomalous_evaluation(e):
                anomaly_count += 1
                console.print(
                    f"[yellow]Anomaly filtered: {e.evaluator_key} -> "
                    f"{e.evaluated_key} on {e.task_id} "
                    f"(scores: acc={e.scores.accuracy}, comp={e.scores.completeness}, "
                    f"logic={e.scores.logical_consistency}, clar={e.scores.clarity}, "
                    f"orig={e.scores.originality})[/yellow]"
                )
                continue
            valid_evals.append(e)

    if anomaly_count > 0:
        console.print(
            f"[yellow]Filtered {anomaly_count} anomalous evaluation(s) "
            f"from profiling.[/yellow]"
        )

    # Group: (evaluated_key, domain) -> {criterion: [scores]}
    domain_scores: dict[
        tuple[str, str], dict[str, list[float]]
    ] = defaultdict(lambda: defaultdict(list))

    for e in valid_evals:
        domain = task_domains.get(e.task_id, "unknown")
        key = (e.evaluated_key, domain)
        scores = e.scores
        domain_scores[key]["accuracy"].append(scores.accuracy)
        domain_scores[key]["completeness"].append(scores.completeness)
        domain_scores[key]["logical_consistency"].append(
            scores.logical_consistency
        )
        domain_scores[key]["clarity"].append(scores.clarity)
        domain_scores[key]["originality"].append(scores.originality)

    # Build model profiles
    all_models = set()
    for e in valid_evals:
        all_models.add(e.evaluated_key)

    profiles: dict[str, ModelProfile] = {}

    for model_key in all_models:
        profile = ModelProfile(
            model_key=model_key,
            display_name=model_display_names.get(model_key, model_key),
        )

        # Get bias info for correction
        bias_metrics = bias_report.model_metrics.get(model_key)

        # Build domain profiles
        all_weighted_scores = []

        for (eval_key, domain), criterion_scores in domain_scores.items():
            if eval_key != model_key:
                continue

            dp = DomainProfile(domain=domain)

            for criterion in CRITERIA:
                raw_values = criterion_scores.get(criterion, [])
                if not raw_values:
                    continue

                raw_avg = float(np.mean(raw_values))
                dp.raw_scores[criterion] = raw_avg

                # Apply bias correction based on evaluator harshness
                corrected = _apply_bias_correction(
                    raw_avg, bias_report, model_key
                )
                dp.corrected_scores[criterion] = corrected

            dp.sample_count = len(
                criterion_scores.get("accuracy", [])
            )

            # Weighted score
            weights = DEFAULT_WEIGHTS
            weighted = sum(
                dp.corrected_scores.get(c, 0) * weights.get(c, 0.2)
                for c in CRITERIA
            )
            dp.weighted_score = weighted
            all_weighted_scores.append(weighted)

            profile.domain_profiles[domain] = dp

        # Overall score
        if all_weighted_scores:
            profile.overall_score = float(np.mean(all_weighted_scores))

        # Evaluator reliability from meta-eval
        if bias_metrics:
            profile.evaluator_reliability = bias_metrics.meta_reliability
            profile.bias_summary = {
                "self_bias": bias_metrics.self_bias_score,
                "series_bias": bias_metrics.series_bias_score,
                "harshness": bias_metrics.harshness_index,
                "consistency": bias_metrics.consistency_score,
            }

        # Identify strengths and weaknesses
        profile.strengths, profile.weaknesses = _identify_strengths_weaknesses(
            profile
        )

        profiles[model_key] = profile

    # Build rankings per domain
    domains = set()
    for _, domain in domain_scores.keys():
        domains.add(domain)

    rankings: dict[str, list[str]] = {}
    for domain in domains:
        domain_model_scores = []
        for model_key, profile in profiles.items():
            dp = profile.domain_profiles.get(domain)
            if dp:
                domain_model_scores.append(
                    (model_key, dp.weighted_score)
                )
        domain_model_scores.sort(key=lambda x: x[1], reverse=True)
        rankings[domain] = [m[0] for m in domain_model_scores]

    # Overall ranking
    overall_ranking = sorted(
        profiles.items(), key=lambda x: x[1].overall_score, reverse=True
    )
    rankings["overall"] = [m[0] for m in overall_ranking]

    # Disagreement analysis
    disagreements = _find_disagreements(evaluations, task_domains)

    # Generate insights
    insights = _generate_insights(profiles, rankings, bias_report)

    return PerformanceReport(
        model_profiles=profiles,
        rankings=rankings,
        disagreements=disagreements,
        insights=insights,
    )


def _is_anomalous_evaluation(e: CrossEvaluation) -> bool:
    """
    Detect corrupted or implausible evaluation data.

    Returns True if the evaluation should be excluded from profiling.

    Detection heuristics:
    1. All scores are 0.0 (total failure, but error=null due to partial parse)
    2. All scores are identical and non-zero (e.g., all 5.0 — likely default fill)
    3. Extreme outlier pattern: most scores are very high (>=9) but one is 0.0
       (indicates partial JSON repair where one field was lost)
    4. Empty reasoning with non-trivial scores but missing strengths/weaknesses
       (indicates a repaired but incomplete evaluation)
    """
    s = e.scores
    values = [s.accuracy, s.completeness, s.logical_consistency,
              s.clarity, s.originality]

    # Heuristic 1: All zeros (total parse failure that slipped through)
    if all(v == 0.0 for v in values):
        return True

    # Heuristic 2: All identical non-zero values (suspicious uniform fill)
    if len(set(values)) == 1 and values[0] != 0.0:
        return True

    # Heuristic 3: Extreme outlier — one score is 0 while others are >= 8
    non_zero = [v for v in values if v > 0.0]
    zero_count = values.count(0.0)
    if zero_count >= 1 and non_zero and min(non_zero) >= 8.0:
        return True

    # Heuristic 4: Very high scores (avg >= 9) with empty reasoning and
    # empty strengths — likely a partially recovered JSON
    avg = sum(values) / len(values)
    if (avg >= 8.0 and not e.reasoning and not e.strengths
            and not e.weaknesses):
        return True

    return False


def _apply_bias_correction(
    raw_score: float,
    bias_report: BiasReport,
    evaluated_model: str,
) -> float:
    """
    Apply bias correction to a raw score.

    Adjusts for evaluator harshness/leniency using the bias report.
    Currently uses a simple global adjustment; could be extended to
    per-evaluator, per-pair adjustments.
    """
    # Get all evaluators' harshness indices
    corrections = []
    for model_key, metrics in bias_report.model_metrics.items():
        if model_key != evaluated_model:
            corrections.append(metrics.harshness_index)

    if not corrections:
        return raw_score

    # Average evaluator harshness correction
    avg_correction = float(np.mean(corrections))

    # Subtract the average bias (if evaluators are harsh, boost score)
    corrected = raw_score - avg_correction
    return max(0.0, min(10.0, corrected))


def _identify_strengths_weaknesses(
    profile: ModelProfile,
) -> tuple[list[str], list[str]]:
    """Identify top strengths and weaknesses from domain profiles."""
    strengths = []
    weaknesses = []

    # Find best/worst domains
    domain_scores = []
    for domain, dp in profile.domain_profiles.items():
        domain_scores.append((domain, dp.weighted_score))

    if not domain_scores:
        return strengths, weaknesses

    domain_scores.sort(key=lambda x: x[1], reverse=True)

    # Top domains = strengths
    for domain, score in domain_scores[:2]:
        if score >= 6.0:
            strengths.append(f"Strong in {domain} (score: {score:.1f})")

    # Bottom domains = weaknesses
    for domain, score in domain_scores[-2:]:
        if score < 6.0:
            weaknesses.append(f"Weak in {domain} (score: {score:.1f})")

    # Find best/worst criteria across all domains
    criterion_avgs: dict[str, list[float]] = defaultdict(list)
    for dp in profile.domain_profiles.values():
        for c, score in dp.corrected_scores.items():
            criterion_avgs[c].append(score)

    for criterion, scores in criterion_avgs.items():
        avg = float(np.mean(scores))
        if avg >= 8.0:
            strengths.append(f"Excellent {criterion} ({avg:.1f}/10)")
        elif avg < 5.0:
            weaknesses.append(f"Low {criterion} ({avg:.1f}/10)")

    return strengths[:5], weaknesses[:5]  # Cap at 5 each


def _find_disagreements(
    evaluations: dict[str, list[CrossEvaluation]],
    task_domains: dict[str, str],
) -> list[DisagreementCase]:
    """Find cases where evaluators significantly disagreed."""
    disagreements = []

    # Group by (task_id, evaluated_key)
    grouped: dict[tuple[str, str], list[CrossEvaluation]] = defaultdict(list)
    for evals in evaluations.values():
        for e in evals:
            if not e.error and not e.is_self_bias_test:
                grouped[(e.task_id, e.evaluated_key)].append(e)

    for (task_id, evaluated_key), evals in grouped.items():
        if len(evals) < 2:
            continue

        evaluator_scores = {}
        for e in evals:
            evaluator_scores[e.evaluator_key] = _avg_score(e)

        scores = list(evaluator_scores.values())
        score_range = max(scores) - min(scores)
        std_dev = float(np.std(scores))

        # Flag if range > 3 or std > 1.5
        if score_range > 3.0 or std_dev > 1.5:
            reasons = []
            if score_range > 4.0:
                reasons.append("Very high score range indicates subjective task")
            if std_dev > 2.0:
                reasons.append("High variance may indicate ambiguous criteria")

            # Check if the disagreement aligns with provider lines
            google_scores = [
                s for k, s in evaluator_scores.items()
                if k.startswith("gemini")
            ]
            anthropic_scores = [
                s for k, s in evaluator_scores.items()
                if k.startswith("opus")
            ]
            openai_scores = [
                s for k, s in evaluator_scores.items()
                if k.startswith("gpt")
            ]

            if (google_scores and anthropic_scores and
                    abs(np.mean(google_scores) - np.mean(anthropic_scores)) > 2.0):
                reasons.append("Scores diverge along provider lines")

            disagreements.append(
                DisagreementCase(
                    task_id=task_id,
                    evaluated_key=evaluated_key,
                    evaluator_scores=evaluator_scores,
                    score_range=score_range,
                    std_deviation=std_dev,
                    possible_reasons=reasons,
                )
            )

    disagreements.sort(key=lambda x: x.score_range, reverse=True)
    return disagreements[:20]  # Top 20 disagreements


def _generate_insights(
    profiles: dict[str, ModelProfile],
    rankings: dict[str, list[str]],
    bias_report: BiasReport,
) -> list[str]:
    """Generate human-readable insights from the analysis."""
    insights = []

    # Overall ranking insight
    overall = rankings.get("overall", [])
    if overall:
        top = overall[0]
        display = profiles[top].display_name or top
        insights.append(
            f"Overall top performer: {display} "
            f"(score: {profiles[top].overall_score:.1f}/10)"
        )

    # Domain-specific leaders
    for domain, ranked_models in rankings.items():
        if domain == "overall" or not ranked_models:
            continue
        leader = ranked_models[0]
        dp = profiles[leader].domain_profiles.get(domain)
        if dp:
            display = profiles[leader].display_name or leader
            insights.append(
                f"Best at {domain}: {display} "
                f"(score: {dp.weighted_score:.1f}/10)"
            )

    # Bias findings
    for model_key, metrics in bias_report.model_metrics.items():
        display = profiles.get(model_key, ModelProfile(model_key=model_key))
        name = display.display_name or model_key

        if abs(metrics.self_bias_score) > 1.0:
            direction = "self-favoring" if metrics.self_bias_score > 0 else "self-critical"
            insights.append(
                f"{name} shows {direction} bias "
                f"(delta: {metrics.self_bias_score:+.1f})"
            )

        if abs(metrics.series_bias_score) > 0.5:
            direction = "favors" if metrics.series_bias_score > 0 else "is harsh toward"
            insights.append(
                f"{name} {direction} same-provider models "
                f"(delta: {metrics.series_bias_score:+.1f})"
            )

    # Most reliable evaluator
    if profiles:
        most_reliable = max(
            profiles.values(),
            key=lambda p: p.evaluator_reliability,
        )
        if most_reliable.evaluator_reliability > 0:
            insights.append(
                f"Most reliable evaluator: "
                f"{most_reliable.display_name or most_reliable.model_key} "
                f"(reliability: {most_reliable.evaluator_reliability:.1f}/10)"
            )

    return insights
