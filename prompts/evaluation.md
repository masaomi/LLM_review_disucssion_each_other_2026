# Cross-Evaluation Prompt Template

This template is used in Layer 1 when one model evaluates another model's
response to a task. The evaluator receives the original task and an
anonymized response.

## System Prompt

```
You are an expert evaluator assessing the quality of an AI model's response.
You must evaluate the response objectively and provide structured scores.
```

## User Prompt Structure

```
## Original Task
{task_prompt}

## Response from {blind_label}
{response_to_evaluate}

## Evaluation Criteria
  - accuracy: Correctness and factual accuracy (0-10)
  - completeness: How thoroughly all aspects are addressed (0-10)
  - logical_consistency: Internal logical coherence (0-10)
  - clarity: Clear expression and organization (0-10)
  - originality: Novel insights and creative approaches (0-10)

## Instructions
Evaluate the response above and return your assessment as JSON with this
exact structure:
{
  "scores": {
    "accuracy": <0-10>,
    "completeness": <0-10>,
    "logical_consistency": <0-10>,
    "clarity": <0-10>,
    "originality": <0-10>
  },
  "strengths": ["<strength 1>", "<strength 2>", ...],
  "weaknesses": ["<weakness 1>", "<weakness 2>", ...],
  "reasoning": "<detailed explanation of your evaluation>"
}

Be fair, specific, and evidence-based. Reference specific parts of the
response to justify scores.
```

## Design Decisions

1. **Blind labels**: Responses are labeled "Model A", "Model B", etc.
   to prevent the evaluator from identifying which model produced the
   response and introducing identification bias.

2. **JSON output**: Structured JSON ensures consistent parsing and
   enables quantitative analysis across evaluations.

3. **Evidence requirement**: "Reference specific parts" encourages
   the evaluator to provide grounded, verifiable assessments rather
   than vague impressions.

4. **Low temperature (0.3)**: Used during evaluation to promote
   consistency and reproducibility across evaluations.
