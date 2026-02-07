"""
Task loader for evaluation task definitions.

Loads YAML task files from the tasks/ directory, organized by domain.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class Task(BaseModel):
    """A single evaluation task."""

    id: str
    domain: str
    difficulty: str = "medium"
    prompt: str
    expected_aspects: list[str] = []
    metadata: dict[str, Any] = {}


class ModelConfig(BaseModel):
    """Configuration for a single LLM model."""

    id: str
    display_name: str
    max_tokens: int = 4096


def load_models(config_path: str = "config/models.yaml") -> dict[str, ModelConfig]:
    """
    Load model configurations from YAML file.

    Args:
        config_path: Path to models.yaml

    Returns:
        Dict mapping model key to ModelConfig
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Model config not found: {config_path}")

    with open(path) as f:
        data = yaml.safe_load(f)

    models = {}
    for key, cfg in data.get("models", {}).items():
        models[key] = ModelConfig(**cfg)

    return models


def load_tasks(
    tasks_dir: str = "tasks",
    domain: str | None = None,
) -> list[Task]:
    """
    Load evaluation tasks from YAML files.

    Args:
        tasks_dir: Root directory containing domain subdirectories
        domain: If specified, only load tasks from this domain

    Returns:
        List of Task objects sorted by domain and id
    """
    root = Path(tasks_dir)
    if not root.exists():
        raise FileNotFoundError(f"Tasks directory not found: {tasks_dir}")

    tasks: list[Task] = []
    domains = [domain] if domain else [d.name for d in root.iterdir() if d.is_dir()]

    for d in sorted(domains):
        domain_dir = root / d
        if not domain_dir.exists():
            continue

        for task_file in sorted(domain_dir.glob("*.yaml")):
            with open(task_file) as f:
                data = yaml.safe_load(f)
            if data:
                tasks.append(Task(**data))

    return tasks


def load_evaluation_criteria(
    config_path: str = "config/evaluation_criteria.yaml",
) -> dict[str, Any]:
    """
    Load evaluation criteria (scoring rubrics) from YAML.

    Args:
        config_path: Path to evaluation_criteria.yaml

    Returns:
        Dict with criteria definitions
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Evaluation criteria not found: {config_path}"
        )

    with open(path) as f:
        return yaml.safe_load(f)
