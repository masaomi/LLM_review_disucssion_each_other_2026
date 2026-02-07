"""
Layer 2: Meta-Evaluation Orchestrator.

Each LLM evaluates the quality of other LLMs' evaluations.
This layer assesses fairness, specificity, coverage, and calibration
of the cross-evaluations performed in Layer 1.
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from .client import OpenRouterClient, parse_json_response
from .evaluator import CrossEvaluation
from .task_loader import ModelConfig, load_models, load_evaluation_criteria

console = Console()


class MetaScores(BaseModel):
    """Structured scores for meta-evaluation."""

    fairness: float = 0.0
    specificity: float = 0.0
    coverage: float = 0.0
    calibration: float = 0.0


class MetaEvaluation(BaseModel):
    """A single meta-evaluation record."""

    task_id: str
    meta_evaluator_key: str  # Model judging the evaluation
    original_evaluator_key: str  # Model that did the original evaluation
    original_evaluated_key: str  # Model that was originally evaluated
    scores: MetaScores
    detected_biases: list[str] = []
    missed_points: list[str] = []
    reasoning: str = ""
    timestamp: float = 0.0
    error: str | None = None


class MetaEvaluator:
    """
    Orchestrates meta-evaluation — evaluating the quality of evaluations.

    For each cross-evaluation in Layer 1, other models assess whether
    the evaluation was fair, specific, comprehensive, and well-calibrated.
    """

    def __init__(
        self,
        client: OpenRouterClient,
        models: dict[str, ModelConfig] | None = None,
        output_dir: str = "results/meta_evaluations",
    ):
        self.client = client
        self.models = models or load_models()
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        try:
            self.criteria = load_evaluation_criteria()
        except FileNotFoundError:
            self.criteria = {}

    def _build_meta_evaluation_prompt(
        self,
        task_prompt: str,
        original_response: str,
        evaluation: CrossEvaluation,
    ) -> str:
        """Build the meta-evaluation prompt."""
        eval_summary = json.dumps(
            {
                "scores": evaluation.scores.model_dump(),
                "strengths": evaluation.strengths,
                "weaknesses": evaluation.weaknesses,
                "reasoning": evaluation.reasoning,
            },
            indent=2,
            ensure_ascii=False,
        )

        criteria_desc = ""
        meta_criteria = self.criteria.get("meta_evaluation", {})
        if meta_criteria:
            for name, info in meta_criteria.get("criteria", {}).items():
                criteria_desc += (
                    f"  - {name}: {info['description']} "
                    f"(weight: {info['weight']})\n"
                )

        return f"""You are a meta-evaluator assessing the QUALITY of an AI evaluation.
Your job is to evaluate how well another AI model evaluated a response.

## Original Task
{task_prompt}

## Response That Was Evaluated
{original_response}

## The Evaluation Being Assessed
{eval_summary}

## Meta-Evaluation Criteria
{criteria_desc if criteria_desc else '''  - fairness: Absence of bias toward or against specific models (0-10)
  - specificity: Concrete, evidence-based reasoning (0-10)
  - coverage: Detection of all significant strengths/weaknesses (0-10)
  - calibration: Appropriate score distribution (0-10)'''}

## Instructions
Assess the quality of the evaluation above. Consider:

1. **Fairness**: Does the evaluator show bias? Are scores justified by evidence?
2. **Specificity**: Does the evaluator reference specific parts of the response?
3. **Coverage**: Did the evaluator catch all important strengths and weaknesses?
4. **Calibration**: Are the scores reasonable given the response quality?

Return your assessment as JSON:
{{
  "scores": {{
    "fairness": <0-10>,
    "specificity": <0-10>,
    "coverage": <0-10>,
    "calibration": <0-10>
  }},
  "detected_biases": ["<bias 1>", "<bias 2>", ...],
  "missed_points": ["<point the evaluator missed>", ...],
  "reasoning": "<detailed analysis of the evaluation quality>"
}}"""

    # Max retries for JSON parse failures (separate from API retries)
    JSON_PARSE_RETRIES = 2

    async def meta_evaluate_single(
        self,
        task_prompt: str,
        original_response: str,
        evaluation: CrossEvaluation,
        meta_evaluator_key: str,
    ) -> MetaEvaluation:
        """
        Perform a single meta-evaluation with JSON parse retry.

        If the model returns malformed JSON that cannot be repaired,
        retries the API call with an explicit reminder to return valid JSON.

        Args:
            task_prompt: Original task prompt
            original_response: The response that was evaluated
            evaluation: The cross-evaluation to assess
            meta_evaluator_key: Model key of the meta-evaluator

        Returns:
            MetaEvaluation result
        """
        meta_config = self.models[meta_evaluator_key]

        prompt = self._build_meta_evaluation_prompt(
            task_prompt=task_prompt,
            original_response=original_response,
            evaluation=evaluation,
        )

        system_msg = (
            "You are an expert meta-evaluator. Assess the quality "
            "of AI evaluations objectively. Always respond with valid JSON only — "
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
                    model_id=meta_config.id,
                    messages=messages,
                    max_tokens=2048,
                    temperature=0.3,
                    json_mode=True,
                )

                meta_data = parse_json_response(result["content"])

                # Validate that essential fields exist
                scores_data = meta_data.get("scores", {})
                if not scores_data or not any(
                    scores_data.get(k) for k in ["fairness", "specificity", "coverage"]
                ):
                    raise ValueError(
                        "Parsed JSON missing required 'scores' fields"
                    )

                scores = MetaScores(
                    fairness=float(scores_data.get("fairness", 0)),
                    specificity=float(scores_data.get("specificity", 0)),
                    coverage=float(scores_data.get("coverage", 0)),
                    calibration=float(scores_data.get("calibration", 0)),
                )

                return MetaEvaluation(
                    task_id=evaluation.task_id,
                    meta_evaluator_key=meta_evaluator_key,
                    original_evaluator_key=evaluation.evaluator_key,
                    original_evaluated_key=evaluation.evaluated_key,
                    scores=scores,
                    detected_biases=meta_data.get("detected_biases", []),
                    missed_points=meta_data.get("missed_points", []),
                    reasoning=meta_data.get("reasoning", ""),
                    timestamp=time.time(),
                )

            except Exception as e:
                last_error = e
                if attempt < self.JSON_PARSE_RETRIES:
                    console.print(
                        f"[yellow]JSON parse retry {attempt + 1}/{self.JSON_PARSE_RETRIES} "
                        f"({meta_evaluator_key} -> {evaluation.evaluator_key}): {e}[/yellow]"
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
            f"[red]Meta-evaluation error ({meta_evaluator_key} -> "
            f"{evaluation.evaluator_key}): {last_error}[/red]"
        )
        return MetaEvaluation(
            task_id=evaluation.task_id,
            meta_evaluator_key=meta_evaluator_key,
            original_evaluator_key=evaluation.evaluator_key,
            original_evaluated_key=evaluation.evaluated_key,
            scores=MetaScores(),
            timestamp=time.time(),
            error=str(last_error),
        )

    async def meta_evaluate_task(
        self,
        task_id: str,
        task_prompt: str,
        task_responses: dict[str, str],
        evaluations: list[CrossEvaluation],
    ) -> list[MetaEvaluation]:
        """
        Run meta-evaluation for all evaluations of a single task.

        Each model meta-evaluates evaluations made by OTHER models
        (a model does not meta-evaluate its own evaluations).

        Args:
            task_id: Task identifier
            task_prompt: Original task prompt
            task_responses: Dict mapping model_key to response text
            evaluations: List of cross-evaluations for this task

        Returns:
            List of MetaEvaluation results
        """
        coros = []
        model_keys = list(self.models.keys())

        for evaluation in evaluations:
            if evaluation.error:
                continue  # Skip failed evaluations

            # Get original response that was evaluated
            original_response = task_responses.get(
                evaluation.evaluated_key, "[Response not available]"
            )

            # Each other model meta-evaluates this evaluation
            for meta_key in model_keys:
                # Don't meta-evaluate your own evaluation
                if meta_key == evaluation.evaluator_key:
                    continue

                coros.append(
                    self.meta_evaluate_single(
                        task_prompt=task_prompt,
                        original_response=original_response,
                        evaluation=evaluation,
                        meta_evaluator_key=meta_key,
                    )
                )

        if not coros:
            return []

        results = await asyncio.gather(*coros, return_exceptions=True)

        valid_results = []
        for result in results:
            if isinstance(result, Exception):
                console.print(
                    f"[red]Unexpected meta-evaluation error: {result}[/red]"
                )
            else:
                valid_results.append(result)

        return valid_results

    async def meta_evaluate_all(
        self,
        evaluations: dict[str, list[CrossEvaluation]],
        task_responses: dict[str, dict[str, str]],
        task_prompts: dict[str, str],
    ) -> dict[str, list[MetaEvaluation]]:
        """
        Run meta-evaluation across all tasks.

        Args:
            evaluations: {task_id: [CrossEvaluation]}
            task_responses: {task_id: {model_key: response_text}}
            task_prompts: {task_id: prompt_text}

        Returns:
            Dict mapping task_id to list of MetaEvaluation
        """
        total_tasks = len(evaluations)
        console.print(
            f"\n[bold]Running meta-evaluation on {total_tasks} tasks...[/bold]\n"
        )

        all_meta: dict[str, list[MetaEvaluation]] = {}

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console,
        ) as progress:
            pbar = progress.add_task(
                "Meta-evaluating...", total=total_tasks
            )

            for task_id, evals in evaluations.items():
                progress.update(
                    pbar,
                    description=f"Meta-eval: {task_id}",
                )

                responses = {
                    k: v for k, v in task_responses.get(task_id, {}).items()
                }
                prompt = task_prompts.get(task_id, "[Prompt not available]")

                meta_evals = await self.meta_evaluate_task(
                    task_id=task_id,
                    task_prompt=prompt,
                    task_responses=responses,
                    evaluations=evals,
                )
                all_meta[task_id] = meta_evals

                # Save incrementally
                self._save_meta_evaluations(task_id, meta_evals)

                progress.update(pbar, advance=1)

        total_metas = sum(len(v) for v in all_meta.values())
        console.print(
            f"\n[green]Completed {total_metas} meta-evaluations. "
            f"Saved to {self.output_dir}/[/green]"
        )

        return all_meta

    def _save_meta_evaluations(
        self, task_id: str, meta_evaluations: list[MetaEvaluation]
    ) -> None:
        """Save meta-evaluations for a single task."""
        output = {
            "task_id": task_id,
            "meta_evaluations": [m.model_dump() for m in meta_evaluations],
        }

        filepath = self.output_dir / f"{task_id}_meta.json"
        with open(filepath, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

    def load_meta_evaluations(
        self, task_id: str | None = None
    ) -> dict[str, list[MetaEvaluation]]:
        """Load previously saved meta-evaluations from disk."""
        all_meta: dict[str, list[MetaEvaluation]] = {}

        if task_id:
            files = [self.output_dir / f"{task_id}_meta.json"]
        else:
            files = sorted(self.output_dir.glob("*_meta.json"))

        for filepath in files:
            if not filepath.exists():
                continue
            with open(filepath) as f:
                data = json.load(f)

            tid = data["task_id"]
            all_meta[tid] = [
                MetaEvaluation(**m) for m in data["meta_evaluations"]
            ]

        return all_meta
