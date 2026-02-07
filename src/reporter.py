"""
Markdown Report Generator.

Generates comprehensive human-readable reports from the analysis data,
embedding charts and providing structured summaries.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .bias_detector import BiasReport
from .profiler import PerformanceReport, CRITERIA
from .client import CostTracker


def generate_report(
    performance_report: PerformanceReport,
    bias_report: BiasReport,
    cost_tracker: CostTracker | None = None,
    chart_paths: list[str] | None = None,
    output_path: str = "results/reports/report.md",
) -> str:
    """
    Generate a comprehensive Markdown report.

    Args:
        performance_report: PerformanceReport with model profiles
        bias_report: BiasReport with bias analysis
        cost_tracker: Optional CostTracker for cost summary
        chart_paths: List of chart image paths to embed
        output_path: Path to save the report

    Returns:
        Path to the saved report
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    sections = []
    sections.append(_header())
    sections.append(_executive_summary(performance_report))
    sections.append(_model_profiles(performance_report))
    sections.append(_rankings(performance_report))
    sections.append(_bias_analysis(bias_report))
    sections.append(_disagreement_analysis(performance_report))
    sections.append(_insights(performance_report))
    sections.append(_charts_section(chart_paths))

    if cost_tracker:
        sections.append(_cost_summary(cost_tracker))

    sections.append(_methodology())

    report = "\n\n".join(s for s in sections if s)

    with open(output_path, "w") as f:
        f.write(report)

    # Also save raw data as JSON
    json_path = output_path.replace(".md", "_data.json")
    raw_data = {
        "performance_report": performance_report.model_dump(),
        "bias_report": bias_report.model_dump(),
        "generated_at": datetime.now().isoformat(),
    }
    with open(json_path, "w") as f:
        json.dump(raw_data, f, indent=2, ensure_ascii=False, default=str)

    return output_path


def _header() -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""# LLM Cross-Evaluation Report

**Generated:** {now}
**System:** LLM Cross-Evaluation & Meta-Evaluation Framework

---"""


def _executive_summary(report: PerformanceReport) -> str:
    lines = ["## Executive Summary", ""]

    if not report.model_profiles:
        return "## Executive Summary\n\nNo data available."

    # Overall ranking
    overall = report.rankings.get("overall", [])
    if overall:
        lines.append("### Overall Rankings")
        lines.append("")
        lines.append("| Rank | Model | Overall Score |")
        lines.append("|------|-------|---------------|")
        for i, model_key in enumerate(overall, 1):
            profile = report.model_profiles[model_key]
            name = profile.display_name or model_key
            lines.append(
                f"| {i} | {name} | {profile.overall_score:.2f}/10 |"
            )

    lines.append("")
    lines.append("### Key Findings")
    lines.append("")
    for insight in report.insights[:5]:
        lines.append(f"- {insight}")

    return "\n".join(lines)


def _model_profiles(report: PerformanceReport) -> str:
    lines = ["## Model Performance Profiles", ""]

    for model_key, profile in sorted(
        report.model_profiles.items(),
        key=lambda x: x[1].overall_score,
        reverse=True,
    ):
        name = profile.display_name or model_key
        lines.append(f"### {name}")
        lines.append("")
        lines.append(f"**Overall Score:** {profile.overall_score:.2f}/10")
        lines.append(
            f"**Evaluator Reliability:** "
            f"{profile.evaluator_reliability:.1f}/10"
        )
        lines.append("")

        # Domain scores table
        if profile.domain_profiles:
            lines.append("#### Scores by Domain")
            lines.append("")
            headers = ["Domain"] + [
                c.replace("_", " ").title() for c in CRITERIA
            ] + ["Weighted"]
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

            for domain, dp in sorted(profile.domain_profiles.items()):
                row = [domain.replace("_", " ").title()]
                for c in CRITERIA:
                    score = dp.corrected_scores.get(c, 0)
                    row.append(f"{score:.1f}")
                row.append(f"**{dp.weighted_score:.1f}**")
                lines.append("| " + " | ".join(row) + " |")

        # Strengths & Weaknesses
        lines.append("")
        if profile.strengths:
            lines.append("**Strengths:**")
            for s in profile.strengths:
                lines.append(f"- {s}")
        if profile.weaknesses:
            lines.append("")
            lines.append("**Weaknesses:**")
            for w in profile.weaknesses:
                lines.append(f"- {w}")

        # Bias summary
        if profile.bias_summary:
            lines.append("")
            lines.append("**Bias Profile (as evaluator):**")
            for bias_type, value in profile.bias_summary.items():
                lines.append(
                    f"- {bias_type.replace('_', ' ').title()}: {value:+.2f}"
                )

        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _rankings(report: PerformanceReport) -> str:
    lines = ["## Domain Rankings", ""]

    for domain, ranked in sorted(report.rankings.items()):
        if domain == "overall":
            continue
        lines.append(
            f"### {domain.replace('_', ' ').title()}"
        )
        lines.append("")
        for i, model_key in enumerate(ranked, 1):
            profile = report.model_profiles.get(model_key)
            name = profile.display_name if profile else model_key
            dp = profile.domain_profiles.get(domain) if profile else None
            score = dp.weighted_score if dp else 0
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
            lines.append(f"{medal} **{name}** — {score:.1f}/10")
        lines.append("")

    return "\n".join(lines)


def _bias_analysis(bias_report: BiasReport) -> str:
    lines = ["## Bias Analysis", ""]

    if not bias_report.model_metrics:
        return "## Bias Analysis\n\nNo bias data available."

    # Metrics table
    lines.append("### Evaluator Bias Metrics")
    lines.append("")
    lines.append(
        "| Model | Self-Bias | Series Bias | "
        "Harshness | Consistency | Meta Reliability |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- |")

    for model_key, metrics in sorted(bias_report.model_metrics.items()):
        lines.append(
            f"| {model_key} | {metrics.self_bias_score:+.2f} | "
            f"{metrics.series_bias_score:+.2f} | "
            f"{metrics.harshness_index:+.2f} | "
            f"{metrics.consistency_score:.2f} | "
            f"{metrics.meta_reliability:.1f} |"
        )

    # Self-bias tests
    if bias_report.self_bias_tests:
        lines.append("")
        lines.append("### Self-Bias Test Results")
        lines.append("")
        lines.append(
            f"Total self-bias tests conducted: "
            f"{len(bias_report.self_bias_tests)}"
        )
        lines.append("")

    # Flagged evaluations
    if bias_report.flagged_evaluations:
        lines.append("### Flagged Evaluations")
        lines.append("")
        lines.append(
            "Evaluations that deviated significantly from consensus:"
        )
        lines.append("")
        for flag in bias_report.flagged_evaluations[:10]:
            lines.append(
                f"- **{flag['evaluator']}** rated "
                f"**{flag['evaluated']}** on {flag['task_id']}: "
                f"{flag['score']:.1f} vs consensus {flag['consensus_mean']:.1f} "
                f"(deviation: {flag['deviation']:.1f}, "
                f"{flag['direction']})"
            )

    return "\n".join(lines)


def _disagreement_analysis(report: PerformanceReport) -> str:
    lines = ["## Disagreement Analysis", ""]

    if not report.disagreements:
        lines.append("No significant disagreements detected.")
        return "\n".join(lines)

    lines.append(
        "Tasks where evaluators significantly disagreed, revealing "
        "differences in evaluation standards or task ambiguity:"
    )
    lines.append("")

    for d in report.disagreements[:10]:
        lines.append(
            f"### {d.task_id} — evaluated: {d.evaluated_key}"
        )
        lines.append("")
        lines.append(
            f"Score range: {d.score_range:.1f}, "
            f"StdDev: {d.std_deviation:.1f}"
        )
        lines.append("")
        for evaluator, score in sorted(
            d.evaluator_scores.items(), key=lambda x: x[1], reverse=True
        ):
            lines.append(f"- {evaluator}: {score:.1f}")
        if d.possible_reasons:
            lines.append("")
            lines.append("Possible reasons:")
            for reason in d.possible_reasons:
                lines.append(f"- {reason}")
        lines.append("")

    return "\n".join(lines)


def _insights(report: PerformanceReport) -> str:
    lines = ["## Key Insights", ""]

    if not report.insights:
        lines.append("No insights generated.")
        return "\n".join(lines)

    for i, insight in enumerate(report.insights, 1):
        lines.append(f"{i}. {insight}")

    return "\n".join(lines)


def _charts_section(chart_paths: list[str] | None) -> str:
    if not chart_paths:
        return ""

    lines = ["## Visualizations", ""]

    for path in chart_paths:
        filename = Path(path).name
        title = (
            filename.replace(".png", "")
            .replace("_", " ")
            .title()
        )
        # Use relative path from report location
        rel_path = Path(path).name
        lines.append(f"### {title}")
        lines.append("")
        lines.append(f"![{title}]({rel_path})")
        lines.append("")

    return "\n".join(lines)


def _cost_summary(cost_tracker: CostTracker) -> str:
    lines = ["## Cost Summary", ""]

    lines.append(f"**Total Cost:** ${cost_tracker.total_cost:.4f}")
    lines.append(f"**Total Tokens:** {cost_tracker.total_tokens:,}")
    lines.append("")

    summary = cost_tracker.summary_by_model()
    if summary:
        lines.append("| Model | Calls | Tokens | Cost | Avg Latency |")
        lines.append("| --- | --- | --- | --- | --- |")
        for model_id, s in sorted(summary.items()):
            lines.append(
                f"| {model_id} | {s['calls']} | "
                f"{s['total_tokens']:,} | ${s['cost_usd']:.4f} | "
                f"{s['avg_latency_ms']:.0f}ms |"
            )

    return "\n".join(lines)


def _methodology() -> str:
    return """## Methodology

### Evaluation Pipeline

1. **Task Execution (Layer 0):** Each model receives identical prompts
   across 6 domains: logic reasoning, code generation, creative writing,
   multilingual, scientific reasoning, and instruction following.

2. **Cross-Evaluation (Layer 1):** Each model evaluates all other models'
   responses using blind labels (Model A/B/C/D) to prevent identification
   bias. Evaluations are structured with 5 criteria scored 0-10.

3. **Meta-Evaluation (Layer 2):** Each model assesses the quality of
   other models' evaluations, checking for fairness, specificity,
   coverage, and calibration.

4. **Bias Detection:** Self-bias tests (20% injection rate), series
   bias analysis, and harshness calibration identify systematic
   evaluation tendencies.

5. **Performance Profiling:** Scores are bias-corrected and aggregated
   into domain profiles, with disagreement analysis highlighting
   areas where models' evaluation standards differ.

### Limitations

- Evaluation quality depends on models' ability to judge, which
  may correlate with their own capabilities.
- Self-bias detection uses statistical inference from limited samples.
- Some evaluation criteria may favor certain model architectures.
- Results should be interpreted as relative comparisons, not absolute
  performance measures."""
