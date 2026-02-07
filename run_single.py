#!/usr/bin/env python3
"""
Quick single-task test runner.

Runs a single task on all models and performs cross-evaluation,
useful for testing and development.

Usage:
  python run_single.py tasks/logic_reasoning/syllogism_01.yaml
  python run_single.py tasks/code_generation/algorithm_01.yaml --models opus_4_5,opus_4_6
"""

import argparse
import asyncio
import json
import sys

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from src.client import OpenRouterClient
from src.task_loader import Task, load_models
from src.executor import TaskExecutor
from src.evaluator import CrossEvaluator

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a single evaluation task for quick testing",
    )
    parser.add_argument(
        "task_file",
        type=str,
        help="Path to a YAML task file.",
    )
    parser.add_argument(
        "--models",
        type=str,
        default=None,
        help="Comma-separated model keys (default: all).",
    )
    parser.add_argument(
        "--skip-eval",
        action="store_true",
        help="Only run task execution, skip cross-evaluation.",
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=1.0,
        help="Max budget in USD (default: $1.00).",
    )
    return parser.parse_args()


async def run_single(args: argparse.Namespace) -> None:
    """Run a single task through execution and optional evaluation."""

    # Load task
    with open(args.task_file) as f:
        task_data = yaml.safe_load(f)
    task = Task(**task_data)

    console.print(Panel(
        f"[bold]Single Task Test: {task.id}[/bold]\n"
        f"Domain: {task.domain} | Difficulty: {task.difficulty}",
        style="blue",
    ))
    console.print(f"\n[bold]Prompt:[/bold]\n{task.prompt}\n")

    # Load models
    all_models = load_models()
    if args.models:
        selected = args.models.split(",")
        models = {k: v for k, v in all_models.items() if k in selected}
    else:
        models = all_models

    console.print(f"[bold]Models:[/bold] {', '.join(models.keys())}\n")

    # Initialize client
    client = OpenRouterClient(budget_usd=args.budget)

    # --- Execute task ---
    executor = TaskExecutor(
        client=client,
        models=models,
        output_dir="results/raw",
    )
    results = await executor.execute_task_on_all_models(task)

    # Display results
    for model_key, result in results.items():
        display_name = models[model_key].display_name
        color = "green" if not result.error else "red"
        console.print(Panel(
            result.response[:2000] if result.response else f"Error: {result.error}",
            title=f"{display_name} ({result.latency_ms:.0f}ms)",
            style=color,
            expand=True,
        ))

    # --- Cross-evaluate ---
    if not args.skip_eval and not any(r.error for r in results.values()):
        console.print("\n[bold]Running cross-evaluation...[/bold]\n")

        evaluator = CrossEvaluator(
            client=client,
            models=models,
            output_dir="results/evaluations",
        )

        evaluations = await evaluator.evaluate_task(
            task_id=task.id,
            task_prompt=task.prompt,
            results=results,
        )

        # Display evaluation summary
        for eval in evaluations:
            if eval.error:
                continue
            scores = eval.scores
            avg = (
                scores.accuracy + scores.completeness
                + scores.logical_consistency + scores.clarity
                + scores.originality
            ) / 5
            console.print(
                f"  {eval.evaluator_key} -> {eval.evaluated_key}: "
                f"avg={avg:.1f} "
                f"(acc={scores.accuracy:.0f} "
                f"comp={scores.completeness:.0f} "
                f"logic={scores.logical_consistency:.0f} "
                f"clar={scores.clarity:.0f} "
                f"orig={scores.originality:.0f})"
            )
            if eval.is_self_bias_test:
                console.print("    [yellow]^ Self-bias test[/yellow]")

    # Cost summary
    console.print(
        f"\n[dim]Total cost: ${client.cost_tracker.total_cost:.4f} | "
        f"Tokens: {client.cost_tracker.total_tokens:,}[/dim]"
    )


def main() -> None:
    args = parse_args()
    asyncio.run(run_single(args))


if __name__ == "__main__":
    main()
