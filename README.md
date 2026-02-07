# LLM Cross-Evaluation & Meta-Evaluation Framework

> **Can AI models objectively judge each other?**
> This framework lets multiple LLMs evaluate each other's responses, then meta-evaluate each other's evaluations — uncovering hidden biases and building reliable performance profiles.

**[日本語版 README はこちら → README_ja.md](README_ja.md)**

---

## Overview

When comparing LLM performance, human evaluation is expensive and benchmarks can be gamed. This framework takes a different approach: **LLMs evaluate each other in a structured, bias-aware pipeline**.

```
Layer 0: Task Execution     → All models answer the same 12 tasks across 6 domains
Layer 1: Cross-Evaluation   → Each model evaluates others' responses (blind, anonymized)
Layer 2: Meta-Evaluation    → Each model evaluates others' evaluation quality
Layer 3: Analysis           → Bias detection, profiling, visualization, reporting
```

### Key Features

- **Blind evaluation** — Responses are anonymized as "Model A/B/C/D" to prevent identification bias
- **Self-bias detection** — 20% of evaluations inject the evaluator's own response to measure self-favoritism
- **Series bias detection** — Detects same-provider favoritism (e.g., Claude 4.5 ↔ Claude 4.6)
- **Bias-corrected scoring** — Adjusts raw scores for evaluator harshness/leniency
- **Disagreement analysis** — Identifies tasks where models diverge, revealing domain-specific preferences
- **OpenRouter integration** — Single API key for accessing all models

---

## Results: Claude Opus 4.5 vs 4.6 vs Gemini 3.0 Pro

Evaluation run on **2026-02-06** with 12 tasks across 6 domains, using 3 evaluator-performer models.

### Overall Rankings

| Rank | Model | Overall Score | Best Domain |
|:----:|-------|:------------:|-------------|
| 1 | **Claude Opus 4.5** | **9.19/10** | Multilingual (9.8) |
| 2 | **Claude Opus 4.6** | **9.03/10** | Logic Reasoning (9.3) |
| 3 | **Gemini 3.0 Pro** | **8.67/10** | Creative Writing (9.5) |

### Domain Performance Comparison

| Domain | Opus 4.5 | Opus 4.6 | Gemini 3.0 Pro | Winner |
|--------|:--------:|:--------:|:--------------:|--------|
| Logic Reasoning | **9.4** | 9.3 | 8.7 | Opus 4.5 |
| Scientific Reasoning | 9.2 | 9.2 | **9.2** | Three-way tie |
| Multilingual | **9.8** | 9.3 | 8.1 | Opus 4.5 |
| Instruction Following | **9.0** | 8.5 | 8.8 | Opus 4.5 |
| Code Generation | **9.2** | 8.8 | 7.7 | Opus 4.5 |
| Creative Writing | 8.5 | 9.1 | **9.5** | Gemini 3.0 Pro |

### Radar Chart — Overall Capability Comparison

![Radar Chart](results_3models/reports/radar_chart.png)

### Domain Comparison

![Domain Comparison](results_3models/reports/domain_comparison.png)

### Evaluator Bias Analysis

| Model | Self-Bias | Series Bias | Harshness | Consistency | Meta Reliability |
|-------|:---------:|:-----------:|:---------:|:-----------:|:----------------:|
| Claude Opus 4.5 | -0.25 | +0.31 | -0.04 | 0.59 | 8.3/10 |
| Claude Opus 4.6 | +0.66 | +0.47 | -0.28 | 1.32 | 8.5/10 |
| Gemini 3.0 Pro | -0.77 | +0.00 | +0.36 | 1.47 | 5.4/10 |

![Bias Plot](results_3models/reports/bias_plot.png)

### Key Insights

1. **Opus 4.5 leads overall** but loses Creative Writing to Gemini and ties Scientific Reasoning with both competitors
2. **Gemini 3.0 Pro wins Creative Writing (9.5)** — a unique strength invisible in Claude-only evaluations
3. **Claude models show series bias** — both Opus 4.5 (+0.31) and 4.6 (+0.47) favor each other, while Gemini shows zero series bias (+0.00)
4. **Gemini is the harshest evaluator** (+0.36) and the most self-critical (-0.77), but with lower reliability (5.4/10)
5. **Opus 4.6 is the most reliable meta-evaluator** (8.5/10) but shows the highest self-bias (+0.66)
6. **Scientific Reasoning has converged** — all three frontier models score 9.2

### The Peer Evaluation Paradox

A critical finding of this study: **the most reliable evaluator (Opus 4.6, meta-reliability 8.5/10) ranks second in overall performance (8.98 vs 4.5's 9.12).** This raises a fundamental question — is the strictest, most discerning model undervalued by less sophisticated evaluators?

Key observations:
- Opus 4.6 is the **harshest evaluator** (-0.28) and the **most reliable meta-evaluator** (8.5/10) — yet scores lower than Opus 4.5
- Opus 4.6 leads in **Originality** (8.1 vs 7.9) across both runs, suggesting deeper thinking
- The 0.14-point gap is **within statistical noise** for 12 tasks
- As a "thinking model," 4.6's strict self-verification may produce advantages (fewer bugs, better UI consistency, edge case handling) that are **invisible to output-only evaluation**

**Most balanced conclusion:** Both models have near-equivalent overall capability with different profiles — 4.5 optimizes for precision and completeness, 4.6 for depth, originality, and critical self-verification. The latter advantages likely manifest more strongly in complex, long-context real-world tasks (large codebase maintenance, nuanced debugging, holistic UI design) that our short-answer scoring rubric cannot capture.

See [analysis/analysis_combined_en_20260206.md](analysis/analysis_combined_en_20260206.md) for the full discussion.

### Note on Data Quality and Gemini API Reliability

**Anomaly filtering** is applied to remove corrupted evaluation data before score aggregation. Gemini 3.0 Pro experiences a **~10-17% structured output failure rate** — returning empty responses, unterminated JSON strings, or partially valid JSON. The framework handles this through:
- Multi-strategy JSON repair pipeline (5 repair strategies)
- Automatic retries with explicit JSON format reminders (2 retries per failure)
- Anomaly detection heuristics (filters partial repair artifacts like scores of 10/10/10/10/0)

These measures reduced evaluation errors by 80% compared to unrepaired runs. However, Gemini's remaining failures reduce its evaluation sample coverage and lower its meta-reliability score. **API reliability is treated as a real-world performance dimension** — Gemini excels at creative and scientific tasks but requires robust error handling for production use.

---

## Quick Start

### 1. Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your OpenRouter API key
```

### 2. Run a Single Task (Quick Test)

```bash
python run_single.py tasks/logic_reasoning/syllogism_01.yaml
python run_single.py tasks/code_generation/algorithm_01.yaml --models opus_4_5 opus_4_6 gemini_3_pro
```

### 3. Run Full Pipeline

```bash
# All tasks, all models
python run_pipeline.py

# With separate output directory (preserves previous results)
python run_pipeline.py --output-dir results_3models

# Specific domain only
python run_pipeline.py --tasks code_generation

# With budget limit
python run_pipeline.py --budget 5.0

# Re-run analysis on existing data
python run_pipeline.py --skip-execution --skip-evaluation --output-dir results_3models
```

---

## Configuration

### Models (`config/models.yaml`)

Define which models to evaluate. Update model IDs to match [OpenRouter's model list](https://openrouter.ai/models):

```yaml
models:
  opus_4_5:
    id: "anthropic/claude-opus-4.5"
    display_name: "Claude Opus 4.5"
    max_tokens: 4096

  opus_4_6:
    id: "anthropic/claude-opus-4.6"
    display_name: "Claude Opus 4.6"
    max_tokens: 4096

  gemini_3_pro:
    id: "google/gemini-3-pro-preview"
    display_name: "Gemini 3.0 Pro"
    max_tokens: 4096
```

### Tasks (`tasks/`)

12 YAML task files organized by domain:

```
tasks/
├── logic_reasoning/         # Syllogisms, paradoxes
├── code_generation/         # Algorithms, debugging
├── creative_writing/        # Metaphors, storytelling
├── multilingual/            # Translation, cross-lingual
├── scientific_reasoning/    # Hypothesis, data interpretation
└── instruction_following/   # Constrained output, formatting
```

### Evaluation Criteria (`config/evaluation_criteria.yaml`)

Configurable scoring rubrics for cross-evaluation and meta-evaluation, with domain-specific weight overrides.

---

## Output

Results are saved to the output directory (default: `results/`):

```
results/
├── raw/                        # Raw model responses (JSON)
├── evaluations/                # Cross-evaluation results with scores and reasoning
├── meta_evaluations/           # Meta-evaluation results
└── reports/
    ├── report.md               # Full Markdown report
    ├── report_data.json        # Raw analysis data (JSON)
    ├── radar_chart.png         # Overall capability comparison
    ├── evaluation_heatmap.png  # Who scored whom and how
    ├── bias_plot.png           # Evaluator bias visualization
    ├── domain_comparison.png   # Per-domain performance bars
    └── radar_*.png             # Per-domain radar charts
```

---

## CLI Options

```
python run_pipeline.py [options]

  --tasks DOMAIN        Run specific domain only
  --models MODEL,...    Use specific models only
  --rounds N            Number of evaluation rounds (default: 1)
  --skip-execution      Skip Layer 0, use existing results
  --skip-evaluation     Skip Layer 1, use existing evaluations
  --skip-meta           Skip Layer 2, use existing meta-evaluations
  --budget AMOUNT       Maximum API spend in USD
  --output-dir DIR      Output directory (default: results/)
```

---

## Methodology

### Evaluation Pipeline

1. **Task Execution (Layer 0):** Each model receives identical prompts across 6 domains (12 tasks total).

2. **Cross-Evaluation (Layer 1):** Each model evaluates all other models' responses using blind labels (Model A/B/C/D) to prevent identification bias. Evaluations score 5 criteria (0–10):
   - **Accuracy** — Correctness and factual reliability
   - **Completeness** — Coverage of all task aspects
   - **Logical Consistency** — Internal coherence of reasoning
   - **Clarity** — Expression quality and organization
   - **Originality** — Novel insights and creative approaches

3. **Meta-Evaluation (Layer 2):** Each model assesses the quality of other models' evaluations on 4 criteria (0–10):
   - **Fairness** — Absence of bias
   - **Specificity** — Evidence-based reasoning
   - **Coverage** — Detection of all important points
   - **Calibration** — Appropriate score distribution

4. **Bias Detection:** Self-bias tests (20% injection rate), series bias analysis, and harshness calibration identify systematic evaluation tendencies.

5. **Performance Profiling:** Scores are bias-corrected and aggregated into domain profiles, with disagreement analysis highlighting areas where models' evaluation standards differ.

### Limitations

- Small sample size (2 tasks per domain) limits statistical confidence
- Evaluation quality depends on models' own capabilities — weaker models may be less reliable evaluators
- Self-bias detection uses statistical inference from limited samples
- JSON parsing issues with some models (e.g., Gemini) can reduce evaluation coverage
- Results should be interpreted as relative comparisons, not absolute performance measures

---

## Cost

A typical 3-model run with 12 tasks costs approximately **$3–5 USD** via OpenRouter. Use `--budget` to set a spending cap.

| Phase | API Calls | Description |
|-------|:---------:|-------------|
| Task Execution | 36 | 12 tasks × 3 models |
| Cross-Evaluation | 72 | 12 tasks × 6 evaluator-performer pairs |
| Meta-Evaluation | 126 | 12 tasks × ~10.5 meta-evaluation pairs |
| **Total** | **~234** | |

---

## Project Structure

```
├── config/                  # Model and evaluation criteria configs
├── tasks/                   # Task definitions (YAML)
├── src/
│   ├── client.py            # OpenRouter API client with retry & cost tracking
│   ├── executor.py          # Layer 0: Task execution
│   ├── evaluator.py         # Layer 1: Cross-evaluation with blind labels
│   ├── meta_evaluator.py    # Layer 2: Meta-evaluation
│   ├── bias_detector.py     # Bias analysis (self, series, harshness)
│   ├── profiler.py          # Performance profiling & aggregation
│   ├── visualizer.py        # Chart generation (matplotlib/plotly)
│   └── reporter.py          # Markdown report generation
├── prompts/                 # Prompt design documentation
├── log/                     # Analysis writeups (EN/JA)
├── run_pipeline.py          # Full pipeline entry point
├── run_single.py            # Quick single-task tester
└── results_3models/         # Latest 3-model evaluation results
```

---

## License

MIT
