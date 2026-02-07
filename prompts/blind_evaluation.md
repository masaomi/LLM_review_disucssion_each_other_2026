# Blind Evaluation Design

This document describes the blind evaluation methodology and
self-bias test injection mechanism.

## Blind Labeling

All responses are anonymized before being presented to evaluators:
- Each evaluator sees responses labeled "Model A", "Model B", "Model C", etc.
- The mapping between labels and actual models is shuffled per evaluator
  to prevent positional bias.
- Labels are consistent within a single evaluation session for the
  same evaluator (Model A always refers to the same model).

## Self-Bias Test Injection

To measure whether models favor their own responses, 20% of evaluations
include a self-bias test:

1. The evaluator's own response is substituted in place of another
   model's response.
2. The evaluator doesn't know this is their own response.
3. If the evaluator consistently scores these higher than their actual
   ratings of other models, self-bias is detected.

### Self-Bias Score Calculation

```
self_bias = mean(scores on own responses as others)
          - mean(scores on actual other responses)
```

- Positive value: Model favors its own responses (self-promoting bias)
- Negative value: Model is harsher on its own responses (self-critical)
- Near zero: No significant self-bias

## Series Bias Detection

Models from the same provider may exhibit "family bias":

```
series_bias = mean(scores given to same-provider models)
            - mean(scores given to different-provider models)
```

This detects whether, e.g., Opus 4.5 systematically rates Opus 4.6
higher (or lower) than Gemini or GPT models.

## Harshness Calibration

Each evaluator's scoring tendency is measured:

```
harshness_index = evaluator's mean score - global mean score
```

- Positive: The evaluator is more lenient than average
- Negative: The evaluator is harsher than average

This index is used for bias correction in the profiling step.
