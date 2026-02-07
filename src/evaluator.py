"""
Layer 1: Cross-Evaluation Orchestrator.

Each LLM evaluates the responses of other LLMs using blind evaluation.
Includes self-bias test injection where a model's own response is
occasionally presented as another model's response.
"""

import asyncio
import json
import random
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from .client import OpenRouterClient, parse_json_response
from .executor import TaskResult
from .task_loader import ModelConfig, load_models, load_evaluation_criteria

console = Console()

# Labels used for blind evaluation (models are anonymized)
BLIND_LABELS = ["Model A", "Model B", "Model C", "Model D"]

# Probability of injecting self-bias test (own response shown as another's)
SELF_BIAS_TEST_PROBABILITY = 0.20


class EvaluationScores(BaseModel):
    """Structured scores from a cross-evaluation."""

    accuracy: float = 0.0
    completeness: float = 0.0
    logical_consistency: float = 0.0
    clarity: float = 0.0
    originality: float = 0.0


class CrossEvaluation(BaseModel):
    """A single cross-evaluation record."""

    task_id: str
    evaluator_key: str  # Model doing the evaluation
    evaluated_key: str  # Model being evaluated
    blind_label: str  # Anonymized label shown to evaluator
    scores: EvaluationScores
    strengths: list[str] = []
    weaknesses: list[str] = []
    reasoning: str = ""
    is_self_bias_test: bool = False  # Was this a self-bias injection?
    timestamp: float = 0.0
    error: str | None = None


class CrossEvaluator:
    """
    Orchestrates cross-evaluation between LLM models.

    For each task, each model evaluates the other models' responses
    using blind labels to prevent identification bias.
    """

    def __init__(
        self,
        client: OpenRouterClient,
        models: dict[str, ModelConfig] | None = None,
        output_dir: str = "results/evaluations",
        self_bias_probability: float = SELF_BIAS_TEST_PROBABILITY,
    ):
        self.client = client
        self.models = models or load_models()
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.self_bias_probability = self_bias_probability

        # Load evaluation criteria for prompt construction
        try:
            self.criteria = load_evaluation_criteria()
        except FileNotFoundError:
            self.criteria = {}

    def _build_evaluation_prompt(
        self,
        task_prompt: str,
        response_to_evaluate: str,
        blind_label: str,
    ) -> str:
        """Build the evaluation prompt with blind labeling."""
        criteria_desc = ""
        cross_eval = self.criteria.get("cross_evaluation", {})
        if cross_eval:
            for name, info in cross_eval.get("criteria", {}).items():
                criteria_desc += (
                    f"  - {name}: {info['description']} "
                    f"(weight: {info['weight']})\n"
                )

        return f"""You are an expert evaluator assessing the quality of an AI model's response.
You must evaluate the response objectively and provide structured scores.

## Original Task
{task_prompt}

## Response from {blind_label}
{response_to_evaluate}

## Evaluation Criteria
{criteria_desc if criteria_desc else '''  - accuracy: Correctness and factual accuracy (0-10)
  - completeness: How thoroughly all aspects are addressed (0-10)
  - logical_consistency: Internal logical coherence (0-10)
  - clarity: Clear expression and organization (0-10)
  - originality: Novel insights and creative approaches (0-10)'''}

## Instructions
Evaluate the response above and return your assessment as JSON with this exact structure:
{{
  "scores": {{
    "accuracy": <0-10>,
    "completeness": <0-10>,
    "logical_consistency": <0-10>,
    "clarity": <0-10>,
    "originality": <0-10>
  }},
  "strengths": ["<strength 1>", "<strength 2>", ...],
  "weaknesses": ["<weakness 1>", "<weakness 2>", ...],
  "reasoning": "<detailed explanation of your evaluation>"
}}

Be fair, specific, and evidence-based. Reference specific parts of the response to justify scores."""

    def _prepare_evaluation_pairs(
        self,
        task_id: str,
        task_prompt: str,
        results: dict[str, TaskResult],
    ) -> list[dict[str, Any]]:
        """
        Prepare evaluation pairs with blind labeling and self-bias injection.

        Returns list of evaluation pair configs, each containing:
        - evaluator_key: Who evaluates
        - evaluated_key: Who is being evaluated
        - blind_label: Anonymized label
        - response: The response text to evaluate
        - is_self_bias_test: Whether this is a self-bias injection
        """
        model_keys = list(results.keys())
        pairs = []

        for evaluator_key in model_keys:
            # Determine targets: all other models
            targets = [k for k in model_keys if k != evaluator_key]

            # Create shuffled blind labels for this evaluator
            available_labels = BLIND_LABELS[: len(targets)]
            label_mapping = dict(zip(targets, available_labels))

            for target_key in targets:
                # Self-bias test: occasionally swap the target response
                # with the evaluator's own response
                is_self_test = random.random() < self.self_bias_probability
                if is_self_test and results[evaluator_key].response:
                    response_text = results[evaluator_key].response
                else:
                    response_text = results[target_key].response
                    is_self_test = False

                pairs.append({
                    "task_id": task_id,
                    "task_prompt": task_prompt,
                    "evaluator_key": evaluator_key,
                    "evaluated_key": target_key,
                    "blind_label": label_mapping[target_key],
                    "response": response_text,
                    "is_self_bias_test": is_self_test,
                })

        return pairs

    # Max retries for JSON parse failures (separate from API retries)
    JSON_PARSE_RETRIES = 2

    async def evaluate_single(
        self,
        pair: dict[str, Any],
    ) -> CrossEvaluation:
        """
        Perform a single cross-evaluation with JSON parse retry.

        If the model returns malformed JSON that cannot be repaired,
        retries the API call with an explicit reminder to return valid JSON.

        Args:
            pair: Evaluation pair configuration

        Returns:
            CrossEvaluation result
        """
        evaluator_key = pair["evaluator_key"]
        evaluator_config = self.models[evaluator_key]

        prompt = self._build_evaluation_prompt(
            task_prompt=pair["task_prompt"],
            response_to_evaluate=pair["response"],
            blind_label=pair["blind_label"],
        )

        system_msg = (
            "You are an expert AI evaluator. Assess responses "
            "objectively. Always respond with valid JSON only — "
            "no markdown, no explanation outside the JSON object."
        )

        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt},
        ]

        last_error = None
        for attempt in range(1 + self.JSON_PARSE_RETRIES):
            try:
                result = await self.client.chat_completion(
                    model_id=evaluator_config.id,
                    messages=messages,
                    max_tokens=2048,
                    temperature=0.3,  # Lower temp for more consistent evals
                    json_mode=True,
                )

                # Parse structured response (with multi-strategy repair)
                eval_data = parse_json_response(result["content"])

                # Validate that essential fields exist
                scores_data = eval_data.get("scores", {})
                if not scores_data or not any(
                    scores_data.get(k) for k in ["accuracy", "completeness", "clarity"]
                ):
                    raise ValueError(
                        "Parsed JSON missing required 'scores' fields"
                    )

                scores = EvaluationScores(
                    accuracy=float(scores_data.get("accuracy", 0)),
                    completeness=float(scores_data.get("completeness", 0)),
                    logical_consistency=float(
                        scores_data.get("logical_consistency", 0)
                    ),
                    clarity=float(scores_data.get("clarity", 0)),
                    originality=float(scores_data.get("originality", 0)),
                )

                return CrossEvaluation(
                    task_id=pair["task_id"],
                    evaluator_key=evaluator_key,
                    evaluated_key=pair["evaluated_key"],
                    blind_label=pair["blind_label"],
                    scores=scores,
                    strengths=eval_data.get("strengths", []),
                    weaknesses=eval_data.get("weaknesses", []),
                    reasoning=eval_data.get("reasoning", ""),
                    is_self_bias_test=pair["is_self_bias_test"],
                    timestamp=time.time(),
                )

            except Exception as e:
                last_error = e
                if attempt < self.JSON_PARSE_RETRIES:
                    console.print(
                        f"[yellow]JSON parse retry {attempt + 1}/{self.JSON_PARSE_RETRIES} "
                        f"({evaluator_key} -> {pair['evaluated_key']}): {e}[/yellow]"
                    )
                    # Add a stronger hint for the retry
                    messages = [
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": prompt},
                        {
                            "role": "user",
                            "content": (
                                "IMPORTANT: Your previous response was not valid JSON. "
                                "Please respond with ONLY a single JSON object. "
                                "Do not include any text before or after the JSON. "
                                "Ensure all strings are properly terminated with closing quotes."
                            ),
                        },
                    ]
                    await asyncio.sleep(1.0)  # Brief pause before retry

        console.print(
            f"[red]Evaluation error ({evaluator_key} -> "
            f"{pair['evaluated_key']}): {last_error}[/red]"
        )
        return CrossEvaluation(
            task_id=pair["task_id"],
            evaluator_key=evaluator_key,
            evaluated_key=pair["evaluated_key"],
            blind_label=pair["blind_label"],
            scores=EvaluationScores(),
            is_self_bias_test=pair["is_self_bias_test"],
            timestamp=time.time(),
            error=str(last_error),
        )

    async def evaluate_task(
        self,
        task_id: str,
        task_prompt: str,
        results: dict[str, TaskResult],
    ) -> list[CrossEvaluation]:
        """
        Run cross-evaluation for a single task across all model pairs.

        Args:
            task_id: Task identifier
            task_prompt: Original task prompt text
            results: Dict of model responses for this task

        Returns:
            List of CrossEvaluation results
        """
        pairs = self._prepare_evaluation_pairs(task_id, task_prompt, results)

        # Run all evaluations in parallel
        coros = [self.evaluate_single(pair) for pair in pairs]
        evaluations = await asyncio.gather(*coros, return_exceptions=True)

        valid_evals = []
        for eval_result in evaluations:
            if isinstance(eval_result, Exception):
                console.print(
                    f"[red]Unexpected evaluation error: {eval_result}[/red]"
                )
            else:
                valid_evals.append(eval_result)

        return valid_evals

    async def evaluate_all(
        self,
        task_results: dict[str, dict[str, TaskResult]],
        tasks: list[Any] | None = None,
    ) -> dict[str, list[CrossEvaluation]]:
        """
        Run cross-evaluation for all tasks.

        Args:
            task_results: Nested dict {task_id: {model_key: TaskResult}}
            tasks: Optional list of Task objects for prompt lookup

        Returns:
            Dict mapping task_id to list of CrossEvaluation
        """
        # Build task prompt lookup
        task_prompts: dict[str, str] = {}
        if tasks:
            for task in tasks:
                task_prompts[task.id] = task.prompt

        total_tasks = len(task_results)
        console.print(
            f"\n[bold]Running cross-evaluation on {total_tasks} tasks "
            f"({len(self.models)} models, "
            f"{len(self.models) * (len(self.models) - 1)} pairs/task)...[/bold]\n"
        )

        all_evaluations: dict[str, list[CrossEvaluation]] = {}

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console,
        ) as progress:
            pbar = progress.add_task(
                "Cross-evaluating...", total=total_tasks
            )

            for task_id, results in task_results.items():
                progress.update(
                    pbar,
                    description=f"Evaluating: {task_id}",
                )

                # Get task prompt (from tasks list or use placeholder)
                task_prompt = task_prompts.get(
                    task_id, "[Task prompt not available]"
                )

                evals = await self.evaluate_task(task_id, task_prompt, results)
                all_evaluations[task_id] = evals

                # Save incrementally
                self._save_evaluations(task_id, evals)

                progress.update(pbar, advance=1)

        # Summary
        total_evals = sum(len(v) for v in all_evaluations.values())
        self_bias_count = sum(
            1
            for evals in all_evaluations.values()
            for e in evals
            if e.is_self_bias_test
        )
        console.print(
            f"\n[green]Completed {total_evals} evaluations "
            f"({self_bias_count} self-bias tests). "
            f"Saved to {self.output_dir}/[/green]"
        )

        return all_evaluations

    def _save_evaluations(
        self, task_id: str, evaluations: list[CrossEvaluation]
    ) -> None:
        """Save evaluations for a single task."""
        output = {
            "task_id": task_id,
            "evaluations": [e.model_dump() for e in evaluations],
        }

        filepath = self.output_dir / f"{task_id}_evals.json"
        with open(filepath, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

    def load_evaluations(
        self, task_id: str | None = None
    ) -> dict[str, list[CrossEvaluation]]:
        """Load previously saved evaluations from disk."""
        all_evals: dict[str, list[CrossEvaluation]] = {}

        if task_id:
            files = [self.output_dir / f"{task_id}_evals.json"]
        else:
            files = sorted(self.output_dir.glob("*_evals.json"))

        for filepath in files:
            if not filepath.exists():
                continue
            with open(filepath) as f:
                data = json.load(f)

            tid = data["task_id"]
            all_evals[tid] = [
                CrossEvaluation(**e) for e in data["evaluations"]
            ]

        return all_evals
