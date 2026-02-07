"""
Layer 0: Task Execution Engine.

Dispatches evaluation tasks to all configured LLM models in parallel,
collects responses, and saves raw results with metadata.
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from .client import OpenRouterClient
from .task_loader import Task, ModelConfig, load_models, load_tasks

console = Console()


class TaskResult:
    """Result of a single model's response to a single task."""

    def __init__(
        self,
        task_id: str,
        model_key: str,
        model_id: str,
        response: str,
        latency_ms: float,
        usage: dict[str, int],
        timestamp: float,
        error: str | None = None,
    ):
        self.task_id = task_id
        self.model_key = model_key
        self.model_id = model_id
        self.response = response
        self.latency_ms = latency_ms
        self.usage = usage
        self.timestamp = timestamp
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "model_key": self.model_key,
            "model_id": self.model_id,
            "response": self.response,
            "latency_ms": self.latency_ms,
            "usage": self.usage,
            "timestamp": self.timestamp,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskResult":
        return cls(**data)


class TaskExecutor:
    """
    Executes evaluation tasks across multiple LLM models.

    Sends the same task prompt to all models in parallel and
    collects their responses.
    """

    def __init__(
        self,
        client: OpenRouterClient,
        models: dict[str, ModelConfig] | None = None,
        output_dir: str = "results/raw",
    ):
        self.client = client
        self.models = models or load_models()
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def execute_task(
        self, task: Task, model_key: str, model_config: ModelConfig
    ) -> TaskResult:
        """
        Execute a single task on a single model.

        Args:
            task: The evaluation task to run
            model_key: Internal model identifier
            model_config: Model configuration with API details

        Returns:
            TaskResult with the model's response
        """
        messages = [
            {
                "role": "system",
                "content": (
                    "You are being evaluated on your capabilities. "
                    "Provide your best, most thorough response to the "
                    "following task. Be precise, complete, and thoughtful."
                ),
            },
            {"role": "user", "content": task.prompt},
        ]

        try:
            result = await self.client.chat_completion(
                model_id=model_config.id,
                messages=messages,
                max_tokens=model_config.max_tokens,
                temperature=0.7,
            )

            return TaskResult(
                task_id=task.id,
                model_key=model_key,
                model_id=model_config.id,
                response=result["content"],
                latency_ms=result["latency_ms"],
                usage=result["usage"],
                timestamp=time.time(),
            )

        except Exception as e:
            console.print(
                f"[red]Error executing {task.id} on {model_key}: {e}[/red]"
            )
            return TaskResult(
                task_id=task.id,
                model_key=model_key,
                model_id=model_config.id,
                response="",
                latency_ms=0,
                usage={},
                timestamp=time.time(),
                error=str(e),
            )

    async def execute_task_on_all_models(
        self, task: Task
    ) -> dict[str, TaskResult]:
        """
        Execute a single task on all configured models in parallel.

        Args:
            task: The evaluation task to run

        Returns:
            Dict mapping model_key to TaskResult
        """
        tasks_coros = {
            key: self.execute_task(task, key, config)
            for key, config in self.models.items()
        }

        results = {}
        gathered = await asyncio.gather(
            *tasks_coros.values(), return_exceptions=True
        )

        for key, result in zip(tasks_coros.keys(), gathered):
            if isinstance(result, Exception):
                results[key] = TaskResult(
                    task_id=task.id,
                    model_key=key,
                    model_id=self.models[key].id,
                    response="",
                    latency_ms=0,
                    usage={},
                    timestamp=time.time(),
                    error=str(result),
                )
            else:
                results[key] = result

        return results

    async def execute_all(
        self,
        tasks: list[Task] | None = None,
        domain: str | None = None,
    ) -> dict[str, dict[str, TaskResult]]:
        """
        Execute all tasks on all models.

        Args:
            tasks: List of tasks to execute (loads from disk if None)
            domain: Filter tasks by domain

        Returns:
            Nested dict: {task_id: {model_key: TaskResult}}
        """
        if tasks is None:
            tasks = load_tasks(domain=domain)

        if not tasks:
            console.print("[yellow]No tasks found to execute.[/yellow]")
            return {}

        console.print(
            f"\n[bold]Executing {len(tasks)} tasks on "
            f"{len(self.models)} models...[/bold]\n"
        )

        all_results: dict[str, dict[str, TaskResult]] = {}

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console,
        ) as progress:
            pbar = progress.add_task(
                "Running tasks...", total=len(tasks)
            )

            for task in tasks:
                progress.update(
                    pbar,
                    description=f"Task: {task.id} ({task.domain})",
                )
                results = await self.execute_task_on_all_models(task)
                all_results[task.id] = results

                # Save incrementally
                self._save_task_results(task.id, results)

                progress.update(pbar, advance=1)

        console.print(
            f"\n[green]Completed {len(tasks)} tasks. "
            f"Results saved to {self.output_dir}/[/green]"
        )

        return all_results

    def _save_task_results(
        self, task_id: str, results: dict[str, TaskResult]
    ) -> None:
        """Save results for a single task to JSON file."""
        output = {
            "task_id": task_id,
            "models": {
                key: result.to_dict() for key, result in results.items()
            },
        }

        filepath = self.output_dir / f"{task_id}.json"
        with open(filepath, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

    def load_results(
        self, task_id: str | None = None
    ) -> dict[str, dict[str, TaskResult]]:
        """
        Load previously saved results from disk.

        Args:
            task_id: If specified, load only this task's results

        Returns:
            Nested dict: {task_id: {model_key: TaskResult}}
        """
        all_results: dict[str, dict[str, TaskResult]] = {}

        if task_id:
            files = [self.output_dir / f"{task_id}.json"]
        else:
            files = sorted(self.output_dir.glob("*.json"))

        for filepath in files:
            if not filepath.exists():
                continue
            with open(filepath) as f:
                data = json.load(f)

            tid = data["task_id"]
            all_results[tid] = {}
            for model_key, result_data in data["models"].items():
                all_results[tid][model_key] = TaskResult.from_dict(result_data)

        return all_results
