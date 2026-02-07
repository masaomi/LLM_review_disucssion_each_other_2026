#!/usr/bin/env python3
"""
LLM Cross-Evaluation Pipeline — Main Entry Point.

Orchestrates the full evaluation pipeline:
  Layer 0: Task Execution
  Layer 1: Cross-Evaluation
  Layer 2: Meta-Evaluation
  Layer 3: Analysis, Visualization, and Reporting

Usage:
  python run_pipeline.py
  python run_pipeline.py --tasks logic_reasoning
  python run_pipeline.py --models opus_4_5,opus_4_6
  python run_pipeline.py --rounds 3
  python run_pipeline.py --skip-execution --skip-evaluation
  python run_pipeline.py --budget 5.0
"""

import argparse
import asyncio
import sys
import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.client import OpenRouterClient
from src.task_loader import load_models, load_tasks
from src.executor import TaskExecutor
from src.evaluator import CrossEvaluator
from src.meta_evaluator import MetaEvaluator
from src.bias_detector import detect_bias
from src.profiler import build_profiles
from src.visualizer import generate_all_charts
from src.reporter import generate_report

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LLM Cross-Evaluation & Meta-Evaluation Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_pipeline.py                        # Full pipeline, all tasks
  python run_pipeline.py --tasks code_generation # Only code tasks
  python run_pipeline.py --skip-execution       # Re-evaluate existing results
  python run_pipeline.py --budget 2.0           # Stop at $2 spend
        """,
    )

    parser.add_argument(
        "--tasks",
        type=str,
        default=None,
        help="Domain to run (e.g., logic_reasoning, code_generation). "
        "Default: all domains.",
    )
    parser.add_argument(
        "--models",
        type=str,
        default=None,
        help="Comma-separated model keys to use "
        "(e.g., opus_4_5,opus_4_6). Default: all models.",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=1,
        help="Number of evaluation rounds (default: 1).",
    )
    parser.add_argument(
        "--skip-execution",
        action="store_true",
        help="Skip task execution, use existing results.",
    )
    parser.add_argument(
        "--skip-evaluation",
        action="store_true",
        help="Skip cross-evaluation, use existing evaluations.",
    )
    parser.add_argument(
        "--skip-meta",
        action="store_true",
        help="Skip meta-evaluation, use existing meta-evaluations.",
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=None,
        help="Maximum budget in USD. Pipeline stops if exceeded.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="results",
        help="Output directory (default: results/).",
    )

    return parser.parse_args()


async def run_pipeline(args: argparse.Namespace) -> None:
    """Execute the full evaluation pipeline."""
    start_time = time.time()

    # --- Setup ---
    console.print(Panel(
        "[bold]LLM Cross-Evaluation & Meta-Evaluation Pipeline[/bold]\n"
        "Evaluating LLM performance through mutual assessment",
        style="blue",
    ))

    # Load models
    all_models = load_models()
    if args.models:
        selected = args.models.split(",")
        models = {k: v for k, v in all_models.items() if k in selected}
        if not models:
            console.print(
                f"[red]No matching models found. "
                f"Available: {list(all_models.keys())}[/red]"
            )
            sys.exit(1)
    else:
        models = all_models

    console.print(f"\n[bold]Models:[/bold] {', '.join(models.keys())}")

    # Load tasks
    tasks = load_tasks(domain=args.tasks)
    if not tasks:
        console.print("[red]No tasks found.[/red]")
        sys.exit(1)
    console.print(f"[bold]Tasks:[/bold] {len(tasks)} across "
                  f"{len(set(t.domain for t in tasks))} domains")
    console.print(f"[bold]Rounds:[/bold] {args.rounds}")
    if args.budget:
        console.print(f"[bold]Budget:[/bold] ${args.budget:.2f}")
    console.print()

    # Initialize client
    client = OpenRouterClient(budget_usd=args.budget)

    # Build task metadata
    task_domains = {t.id: t.domain for t in tasks}
    task_prompts = {t.id: t.prompt for t in tasks}
    display_names = {k: v.display_name for k, v in models.items()}

    for round_num in range(1, args.rounds + 1):
        if args.rounds > 1:
            console.print(Panel(
                f"[bold]Round {round_num} of {args.rounds}[/bold]",
                style="cyan",
            ))

        # --- Layer 0: Task Execution ---
        executor = TaskExecutor(
            client=client,
            models=models,
            output_dir=f"{args.output_dir}/raw",
        )

        if args.skip_execution:
            console.print("[yellow]Skipping execution, loading existing results...[/yellow]")
            task_results = executor.load_results()
            if not task_results:
                console.print("[red]No existing results found. Run without --skip-execution.[/red]")
                sys.exit(1)
        else:
            task_results = await executor.execute_all(tasks=tasks)

        # Build response lookup for meta-evaluation
        task_responses: dict[str, dict[str, str]] = {}
        for task_id, model_results in task_results.items():
            task_responses[task_id] = {
                k: v.response for k, v in model_results.items()
            }

        # --- Layer 1: Cross-Evaluation ---
        evaluator = CrossEvaluator(
            client=client,
            models=models,
            output_dir=f"{args.output_dir}/evaluations",
        )

        if args.skip_evaluation:
            console.print("[yellow]Skipping evaluation, loading existing...[/yellow]")
            evaluations = evaluator.load_evaluations()
            if not evaluations:
                console.print("[red]No existing evaluations found.[/red]")
                sys.exit(1)
        else:
            evaluations = await evaluator.evaluate_all(
                task_results=task_results,
                tasks=tasks,
            )

        # --- Layer 2: Meta-Evaluation ---
        meta_evaluator = MetaEvaluator(
            client=client,
            models=models,
            output_dir=f"{args.output_dir}/meta_evaluations",
        )

        if args.skip_meta:
            console.print("[yellow]Skipping meta-evaluation, loading existing...[/yellow]")
            meta_evaluations = meta_evaluator.load_meta_evaluations()
        else:
            meta_evaluations = await meta_evaluator.meta_evaluate_all(
                evaluations=evaluations,
                task_responses=task_responses,
                task_prompts=task_prompts,
            )

    # --- Layer 3: Analysis ---
    console.print("\n[bold]Analyzing results...[/bold]")

    # Bias detection
    bias_report = detect_bias(evaluations, meta_evaluations)

    # Performance profiling
    performance_report = build_profiles(
        evaluations=evaluations,
        bias_report=bias_report,
        task_domains=task_domains,
        model_display_names=display_names,
    )

    # Visualization
    console.print("[bold]Generating charts...[/bold]")
    chart_paths = generate_all_charts(
        report=performance_report,
        bias_report=bias_report,
        output_dir=f"{args.output_dir}/reports",
        display_names=display_names,
    )

    # Report generation
    console.print("[bold]Generating report...[/bold]")
    report_path = generate_report(
        performance_report=performance_report,
        bias_report=bias_report,
        cost_tracker=client.cost_tracker,
        chart_paths=chart_paths,
        output_path=f"{args.output_dir}/reports/report.md",
    )

    # --- Summary ---
    elapsed = time.time() - start_time

    console.print()
    console.print(Panel(
        "[bold green]Pipeline Complete[/bold green]",
        style="green",
    ))

    # Print summary table
    table = Table(title="Results Summary")
    table.add_column("Metric", style="bold")
    table.add_column("Value")

    table.add_row("Total Time", f"{elapsed:.1f}s")
    table.add_row("Tasks Executed", str(len(task_results)))
    table.add_row(
        "Cross-Evaluations",
        str(sum(len(v) for v in evaluations.values())),
    )
    table.add_row(
        "Meta-Evaluations",
        str(sum(len(v) for v in meta_evaluations.values())),
    )
    table.add_row("API Cost", f"${client.cost_tracker.total_cost:.4f}")
    table.add_row("Total Tokens", f"{client.cost_tracker.total_tokens:,}")
    table.add_row("Report", report_path)
    table.add_row("Charts", str(len(chart_paths)))

    console.print(table)

    # Print top-level insights
    if performance_report.insights:
        console.print("\n[bold]Key Insights:[/bold]")
        for i, insight in enumerate(performance_report.insights[:5], 1):
            console.print(f"  {i}. {insight}")

    console.print(
        f"\n[dim]Full report: {report_path}[/dim]"
    )


def main() -> None:
    args = parse_args()
    asyncio.run(run_pipeline(args))


if __name__ == "__main__":
    main()
