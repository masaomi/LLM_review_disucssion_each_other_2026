"""
Bias Detection Module.

Analyzes cross-evaluation and meta-evaluation data to detect:
- Self-bias: Models favoring their own responses
- Series bias: Models favoring same-provider models (e.g., Opus 4.5 <-> Opus 4.6)
- Harshness/leniency: Systematic scoring tendencies per evaluator
"""

from collections import defaultdict
from typing import Any

import numpy as np
from pydantic import BaseModel

from .evaluator import CrossEvaluation
from .meta_evaluator import MetaEvaluation


class BiasMetrics(BaseModel):
    """Bias metrics for a single model as evaluator."""

    model_key: str
    self_bias_score: float = 0.0  # Difference: self-eval vs others' avg
    series_bias_score: float = 0.0  # Bias toward same-provider models
    harshness_index: float = 0.0  # Negative = harsh, positive = lenient
    consistency_score: float = 0.0  # StdDev across evaluations
    meta_reliability: float = 0.0  # How reliable as evaluator (from meta-eval)


class BiasReport(BaseModel):
    """Complete bias analysis report."""

    model_metrics: dict[str, BiasMetrics] = {}
    evaluation_matrix: dict[str, dict[str, float]] = {}
    self_bias_tests: list[dict[str, Any]] = []
    flagged_evaluations: list[dict[str, Any]] = []


# Map model keys to provider for series-bias detection
PROVIDER_MAP = {
    "opus_4_5": "anthropic",
    "opus_4_6": "anthropic",
    "gemini_3_pro": "google",
}


def _avg_score(eval: CrossEvaluation) -> float:
    """Calculate average score across all criteria."""
    s = eval.scores
    values = [s.accuracy, s.completeness, s.logical_consistency,
              s.clarity, s.originality]
    return np.mean(values).item()


def _is_anomalous_evaluation(e: CrossEvaluation) -> bool:
    """
    Detect corrupted or implausible evaluation data.

    Returns True if the evaluation should be excluded from bias analysis.
    """
    s = e.scores
    values = [s.accuracy, s.completeness, s.logical_consistency,
              s.clarity, s.originality]

    # All zeros
    if all(v == 0.0 for v in values):
        return True

    # All identical non-zero
    if len(set(values)) == 1 and values[0] != 0.0:
        return True

    # One score is 0 while others are >= 8 (partial JSON repair artifact)
    non_zero = [v for v in values if v > 0.0]
    zero_count = values.count(0.0)
    if zero_count >= 1 and non_zero and min(non_zero) >= 8.0:
        return True

    # High scores with empty reasoning/strengths (incomplete repair)
    avg = sum(values) / len(values)
    if (avg >= 8.0 and not e.reasoning and not e.strengths
            and not e.weaknesses):
        return True

    return False


def detect_bias(
    evaluations: dict[str, list[CrossEvaluation]],
    meta_evaluations: dict[str, list[MetaEvaluation]] | None = None,
) -> BiasReport:
    """
    Analyze evaluations for systematic biases.

    Args:
        evaluations: {task_id: [CrossEvaluation]}
        meta_evaluations: Optional {task_id: [MetaEvaluation]}

    Returns:
        BiasReport with detailed bias metrics
    """
    # Flatten all evaluations (excluding anomalies)
    all_evals: list[CrossEvaluation] = []
    for evals in evaluations.values():
        for e in evals:
            if not _is_anomalous_evaluation(e):
                all_evals.append(e)
            elif not e.error:
                # Only log if it wasn't already flagged as an error
                pass  # profiler will log these

    # 1. Build evaluation matrix: evaluator -> evaluated -> [scores]
    eval_matrix: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for e in all_evals:
        if e.error:
            continue
        avg = _avg_score(e)
        eval_matrix[e.evaluator_key][e.evaluated_key].append(avg)

    # 2. Calculate per-model metrics
    model_keys = set()
    for e in all_evals:
        model_keys.add(e.evaluator_key)
        model_keys.add(e.evaluated_key)

    model_metrics: dict[str, BiasMetrics] = {}

    for model_key in model_keys:
        metrics = BiasMetrics(model_key=model_key)

        # -- Self-bias from self-bias tests --
        self_tests = [
            e for e in all_evals
            if e.is_self_bias_test and e.evaluator_key == model_key
        ]
        non_self_evals = [
            e for e in all_evals
            if not e.is_self_bias_test
            and e.evaluator_key == model_key
            and e.error is None
        ]

        if self_tests and non_self_evals:
            self_avg = np.mean([_avg_score(e) for e in self_tests]).item()
            others_avg = np.mean(
                [_avg_score(e) for e in non_self_evals]
            ).item()
            metrics.self_bias_score = self_avg - others_avg

        # -- Series bias --
        own_provider = PROVIDER_MAP.get(model_key, "unknown")
        same_provider_scores = []
        diff_provider_scores = []

        for evaluated_key, scores_list in eval_matrix.get(
            model_key, {}
        ).items():
            evaluated_provider = PROVIDER_MAP.get(evaluated_key, "unknown")
            if (
                evaluated_provider == own_provider
                and evaluated_key != model_key
            ):
                same_provider_scores.extend(scores_list)
            elif evaluated_provider != own_provider:
                diff_provider_scores.extend(scores_list)

        if same_provider_scores and diff_provider_scores:
            metrics.series_bias_score = (
                float(np.mean(same_provider_scores))
                - float(np.mean(diff_provider_scores))
            )

        # -- Harshness index --
        # Compare this evaluator's average score to global average
        all_my_scores = []
        for scores_list in eval_matrix.get(model_key, {}).values():
            all_my_scores.extend(scores_list)

        all_global_scores = []
        for evaluator in eval_matrix.values():
            for scores_list in evaluator.values():
                all_global_scores.extend(scores_list)

        if all_my_scores and all_global_scores:
            metrics.harshness_index = (
                float(np.mean(all_my_scores))
                - float(np.mean(all_global_scores))
            )

        # -- Consistency score (lower = more consistent) --
        if all_my_scores:
            metrics.consistency_score = float(np.std(all_my_scores))

        # -- Meta-reliability (from meta-evaluations) --
        if meta_evaluations:
            all_meta_evals: list[MetaEvaluation] = []
            for metas in meta_evaluations.values():
                all_meta_evals.extend(metas)

            # Scores received as evaluator
            meta_for_model = [
                m for m in all_meta_evals
                if m.original_evaluator_key == model_key and not m.error
            ]
            if meta_for_model:
                reliability_scores = []
                for m in meta_for_model:
                    s = m.scores
                    avg_meta = np.mean(
                        [s.fairness, s.specificity, s.coverage, s.calibration]
                    ).item()
                    reliability_scores.append(avg_meta)
                metrics.meta_reliability = float(np.mean(reliability_scores))

        model_metrics[model_key] = metrics

    # 3. Build summary evaluation matrix (avg scores)
    summary_matrix: dict[str, dict[str, float]] = {}
    for evaluator, evaluated_dict in eval_matrix.items():
        summary_matrix[evaluator] = {}
        for evaluated, scores in evaluated_dict.items():
            summary_matrix[evaluator][evaluated] = float(np.mean(scores))

    # 4. Collect self-bias test details
    self_bias_tests = []
    for e in all_evals:
        if e.is_self_bias_test:
            self_bias_tests.append({
                "task_id": e.task_id,
                "evaluator": e.evaluator_key,
                "evaluated_label": e.evaluated_key,
                "avg_score": _avg_score(e),
                "scores": e.scores.model_dump(),
            })

    # 5. Flag suspicious evaluations
    flagged = _flag_suspicious_evaluations(all_evals, summary_matrix)

    return BiasReport(
        model_metrics=model_metrics,
        evaluation_matrix=summary_matrix,
        self_bias_tests=self_bias_tests,
        flagged_evaluations=flagged,
    )


def _flag_suspicious_evaluations(
    evaluations: list[CrossEvaluation],
    avg_matrix: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    """
    Flag evaluations that deviate significantly from consensus.

    An evaluation is flagged if its average score differs from the
    consensus (other evaluators' average for the same response)
    by more than 2 points.
    """
    flagged = []

    # Group evaluations by (task_id, evaluated_key), excluding anomalies
    grouped: dict[tuple[str, str], list[CrossEvaluation]] = defaultdict(list)
    for e in evaluations:
        if not e.error and not _is_anomalous_evaluation(e):
            grouped[(e.task_id, e.evaluated_key)].append(e)

    for (task_id, evaluated_key), evals in grouped.items():
        if len(evals) < 2:
            continue

        scores = [_avg_score(e) for e in evals]
        mean_score = np.mean(scores)

        for e, score in zip(evals, scores):
            deviation = abs(score - mean_score)
            if deviation > 2.0:
                flagged.append({
                    "task_id": task_id,
                    "evaluator": e.evaluator_key,
                    "evaluated": evaluated_key,
                    "score": score,
                    "consensus_mean": float(mean_score),
                    "deviation": float(deviation),
                    "direction": "lenient" if score > mean_score else "harsh",
                })

    return sorted(flagged, key=lambda x: x["deviation"], reverse=True)
