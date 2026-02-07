"""
Visualization Module.

Generates radar charts, heatmaps, bias plots, and other visualizations
from the performance analysis data.
"""

from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")  # Non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.cm as cm

from .profiler import PerformanceReport, CRITERIA
from .bias_detector import BiasReport


# Color palette for models (colorblind-friendly)
MODEL_COLORS = {
    "opus_4_5": "#D4A574",  # Anthropic Opus 4.5
    "opus_4_6": "#CC785C",  # Anthropic Opus 4.6
}

DEFAULT_COLOR = "#888888"


def _get_color(model_key: str) -> str:
    return MODEL_COLORS.get(model_key, DEFAULT_COLOR)


def generate_radar_chart(
    report: PerformanceReport,
    output_path: str = "results/reports/radar_chart.png",
    domain: str | None = None,
) -> str:
    """
    Generate a radar chart comparing models across evaluation criteria.

    Args:
        report: PerformanceReport with model profiles
        output_path: Path to save the chart
        domain: If specified, show only this domain's scores

    Returns:
        Path to the saved chart
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    criteria_labels = [c.replace("_", " ").title() for c in CRITERIA]
    num_criteria = len(CRITERIA)

    # Calculate angles for radar
    angles = np.linspace(0, 2 * np.pi, num_criteria, endpoint=False).tolist()
    angles += angles[:1]  # Close the polygon

    fig, ax = plt.subplots(figsize=(10, 10), subplot_kw=dict(polar=True))

    for model_key, profile in report.model_profiles.items():
        if domain:
            dp = profile.domain_profiles.get(domain)
            if not dp:
                continue
            values = [
                dp.corrected_scores.get(c, 0) for c in CRITERIA
            ]
        else:
            # Average across all domains
            values = []
            for c in CRITERIA:
                domain_vals = []
                for dp in profile.domain_profiles.values():
                    if c in dp.corrected_scores:
                        domain_vals.append(dp.corrected_scores[c])
                values.append(
                    float(np.mean(domain_vals)) if domain_vals else 0
                )

        values += values[:1]  # Close the polygon

        color = _get_color(model_key)
        display_name = profile.display_name or model_key
        ax.plot(angles, values, "o-", linewidth=2, label=display_name,
                color=color)
        ax.fill(angles, values, alpha=0.1, color=color)

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(criteria_labels, size=11)
    ax.set_ylim(0, 10)
    ax.set_yticks([2, 4, 6, 8, 10])
    ax.set_yticklabels(["2", "4", "6", "8", "10"], size=9)
    ax.grid(True, alpha=0.3)

    title = "Model Performance Comparison"
    if domain:
        title += f" — {domain.replace('_', ' ').title()}"
    ax.set_title(title, size=14, fontweight="bold", pad=20)
    ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1))

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()

    return output_path


def generate_evaluation_heatmap(
    bias_report: BiasReport,
    output_path: str = "results/reports/evaluation_heatmap.png",
    display_names: dict[str, str] | None = None,
) -> str:
    """
    Generate a heatmap of the evaluation matrix.

    Shows average scores each evaluator gave to each evaluated model.

    Args:
        bias_report: BiasReport with evaluation_matrix
        output_path: Path to save the chart
        display_names: {model_key: display_name}

    Returns:
        Path to the saved chart
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    display_names = display_names or {}

    matrix = bias_report.evaluation_matrix
    if not matrix:
        return output_path

    evaluators = sorted(matrix.keys())
    evaluated = sorted(
        set(k for v in matrix.values() for k in v.keys())
    )

    # Build data array
    data = np.zeros((len(evaluators), len(evaluated)))
    for i, ev_or in enumerate(evaluators):
        for j, ev_ed in enumerate(evaluated):
            data[i, j] = matrix.get(ev_or, {}).get(ev_ed, 0)

    fig, ax = plt.subplots(figsize=(10, 8))

    im = ax.imshow(data, cmap="RdYlGn", vmin=0, vmax=10, aspect="auto")

    # Labels
    ev_or_labels = [display_names.get(k, k) for k in evaluators]
    ev_ed_labels = [display_names.get(k, k) for k in evaluated]

    ax.set_xticks(range(len(evaluated)))
    ax.set_xticklabels(ev_ed_labels, rotation=45, ha="right", size=11)
    ax.set_yticks(range(len(evaluators)))
    ax.set_yticklabels(ev_or_labels, size=11)

    ax.set_xlabel("Evaluated Model", size=12)
    ax.set_ylabel("Evaluator Model", size=12)
    ax.set_title("Cross-Evaluation Matrix\n(Average Scores)", size=14,
                 fontweight="bold")

    # Annotate cells
    for i in range(len(evaluators)):
        for j in range(len(evaluated)):
            val = data[i, j]
            text_color = "white" if val < 4 or val > 8 else "black"
            ax.text(j, i, f"{val:.1f}", ha="center", va="center",
                    color=text_color, fontweight="bold", size=12)

    plt.colorbar(im, ax=ax, label="Score (0-10)", shrink=0.8)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()

    return output_path


def generate_bias_plot(
    bias_report: BiasReport,
    output_path: str = "results/reports/bias_plot.png",
    display_names: dict[str, str] | None = None,
) -> str:
    """
    Generate a visualization of model biases.

    Shows self-bias, series bias, and harshness for each model.

    Args:
        bias_report: BiasReport with model metrics
        output_path: Path to save the chart
        display_names: {model_key: display_name}

    Returns:
        Path to the saved chart
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    display_names = display_names or {}

    metrics = bias_report.model_metrics
    if not metrics:
        return output_path

    model_keys = sorted(metrics.keys())
    labels = [display_names.get(k, k) for k in model_keys]

    self_bias = [metrics[k].self_bias_score for k in model_keys]
    series_bias = [metrics[k].series_bias_score for k in model_keys]
    harshness = [metrics[k].harshness_index for k in model_keys]

    x = np.arange(len(model_keys))
    width = 0.25

    fig, ax = plt.subplots(figsize=(12, 6))

    bars1 = ax.bar(x - width, self_bias, width, label="Self-Bias",
                   color="#E74C3C", alpha=0.8)
    bars2 = ax.bar(x, series_bias, width, label="Series Bias",
                   color="#F39C12", alpha=0.8)
    bars3 = ax.bar(x + width, harshness, width, label="Harshness Index",
                   color="#3498DB", alpha=0.8)

    ax.set_xlabel("Model", size=12)
    ax.set_ylabel("Bias Score", size=12)
    ax.set_title("Evaluator Bias Analysis", size=14, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", size=11)
    ax.legend(fontsize=10)
    ax.axhline(y=0, color="black", linewidth=0.5, linestyle="--")
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()

    return output_path


def generate_domain_comparison(
    report: PerformanceReport,
    output_path: str = "results/reports/domain_comparison.png",
) -> str:
    """
    Generate a grouped bar chart comparing models across domains.

    Args:
        report: PerformanceReport with model profiles
        output_path: Path to save the chart

    Returns:
        Path to the saved chart
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Collect all domains
    domains = set()
    for profile in report.model_profiles.values():
        domains.update(profile.domain_profiles.keys())
    domains = sorted(domains)

    if not domains:
        return output_path

    model_keys = sorted(report.model_profiles.keys())
    n_models = len(model_keys)
    n_domains = len(domains)

    fig, ax = plt.subplots(figsize=(max(12, n_domains * 2), 7))

    x = np.arange(n_domains)
    width = 0.8 / n_models

    for i, model_key in enumerate(model_keys):
        profile = report.model_profiles[model_key]
        scores = []
        for domain in domains:
            dp = profile.domain_profiles.get(domain)
            scores.append(dp.weighted_score if dp else 0)

        color = _get_color(model_key)
        display_name = profile.display_name or model_key
        offset = (i - n_models / 2 + 0.5) * width
        ax.bar(x + offset, scores, width, label=display_name,
               color=color, alpha=0.85)

    ax.set_xlabel("Domain", size=12)
    ax.set_ylabel("Weighted Score", size=12)
    ax.set_title("Performance by Domain", size=14, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(
        [d.replace("_", " ").title() for d in domains],
        rotation=45, ha="right", size=11,
    )
    ax.set_ylim(0, 10)
    ax.legend(fontsize=10)
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()

    return output_path


def generate_all_charts(
    report: PerformanceReport,
    bias_report: BiasReport,
    output_dir: str = "results/reports",
    display_names: dict[str, str] | None = None,
) -> list[str]:
    """
    Generate all visualization charts.

    Args:
        report: PerformanceReport
        bias_report: BiasReport
        output_dir: Directory to save charts
        display_names: {model_key: display_name}

    Returns:
        List of paths to generated charts
    """
    paths = []

    paths.append(generate_radar_chart(
        report, f"{output_dir}/radar_chart.png"
    ))
    paths.append(generate_evaluation_heatmap(
        bias_report, f"{output_dir}/evaluation_heatmap.png", display_names
    ))
    paths.append(generate_bias_plot(
        bias_report, f"{output_dir}/bias_plot.png", display_names
    ))
    paths.append(generate_domain_comparison(
        report, f"{output_dir}/domain_comparison.png"
    ))

    # Per-domain radar charts
    domains = set()
    for profile in report.model_profiles.values():
        domains.update(profile.domain_profiles.keys())

    for domain in sorted(domains):
        path = generate_radar_chart(
            report,
            f"{output_dir}/radar_{domain}.png",
            domain=domain,
        )
        paths.append(path)

    return paths
