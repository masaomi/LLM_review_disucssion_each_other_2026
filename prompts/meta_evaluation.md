# Meta-Evaluation Prompt Template

This template is used in Layer 2 when one model evaluates the quality
of another model's evaluation. The meta-evaluator assesses whether the
original evaluation was fair, specific, comprehensive, and well-calibrated.

## System Prompt

```
You are an expert meta-evaluator. Assess the quality of AI evaluations
objectively. Always respond with valid JSON.
```

## User Prompt Structure

```
You are a meta-evaluator assessing the QUALITY of an AI evaluation.
Your job is to evaluate how well another AI model evaluated a response.

## Original Task
{task_prompt}

## Response That Was Evaluated
{original_response}

## The Evaluation Being Assessed
{evaluation_json}

## Meta-Evaluation Criteria
  - fairness: Absence of bias toward or against specific models (0-10)
  - specificity: Concrete, evidence-based reasoning (0-10)
  - coverage: Detection of all significant strengths/weaknesses (0-10)
  - calibration: Appropriate score distribution (0-10)

## Instructions
Assess the quality of the evaluation above. Consider:

1. **Fairness**: Does the evaluator show bias? Are scores justified?
2. **Specificity**: Does the evaluator reference specific parts?
3. **Coverage**: Did the evaluator catch all important points?
4. **Calibration**: Are the scores reasonable given the quality?

Return your assessment as JSON:
{
  "scores": {
    "fairness": <0-10>,
    "specificity": <0-10>,
    "coverage": <0-10>,
    "calibration": <0-10>
  },
  "detected_biases": ["<bias 1>", "<bias 2>", ...],
  "missed_points": ["<point the evaluator missed>", ...],
  "reasoning": "<detailed analysis of the evaluation quality>"
}
```

## Design Decisions

1. **Full context**: The meta-evaluator sees the original task, the
   response, AND the evaluation, enabling it to judge whether the
   evaluation accurately reflects the response quality.

2. **Bias detection**: Explicitly asks for detected biases, which
   feeds into the bias analysis module.

3. **Missed points**: Identifies gaps in the evaluation, revealing
   what each model considers important vs. what it overlooks.

4. **Calibration check**: Assesses whether scores are proportional
   to actual response quality, detecting inflation/deflation.
