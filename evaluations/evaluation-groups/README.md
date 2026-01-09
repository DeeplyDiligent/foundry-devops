# Evaluation Configuration

This directory contains YAML configuration files for running evaluations on AI agents and datasets.

## Configuration Format

```yaml
# Evaluation name and description
name: evaluation-name
description: Description of what this evaluation tests

# Evaluation mode: 'dataset' or 'agent'
mode: agent  # or dataset

# Agent configuration (required for agent mode)
agent:
  name: agent-name
  version: "1"  # Optional - uses latest if not specified

# Test data
data:
  file: path/to/data.jsonl

# Evaluators to run
evaluators:
  # Built-in evaluators
  - name: builtin.relevance
    type: builtin
    parameters:
      deployment_name: gpt-4.1
      threshold: 0.7
  
  # Custom evaluators (not yet fully supported)
  - name: custom-evaluator-name
    type: custom
    parameters:
      deployment_name: gpt-4.1
      threshold: 0.7

# Output configuration
output:
  directory: evaluations/results
  format: json
```

## Available Configurations

### hello-world-eval.yaml
Evaluates the hello-world agent using built-in quality metrics:
- builtin.relevance
- builtin.coherence
- builtin.groundedness

**Usage:**
```bash
python scripts/runtime/run_evaluation.py --config evaluations/evaluation-config/hello-world-eval.yaml
```

### dataset-eval.yaml
Evaluates pre-existing responses from a dataset:
- builtin.relevance
- builtin.coherence
- builtin.fluency

**Usage:**
```bash
python scripts/runtime/run_evaluation.py --config evaluations/evaluation-config/dataset-eval.yaml
```

### custom-evaluators.yaml
Comprehensive evaluation with both built-in and custom evaluators.
Note: Custom evaluators are currently skipped due to API limitations.

## Evaluation Modes

### Agent Mode
In agent mode, Azure AI Foundry will call your agent with the queries from the test data and evaluate the responses against ground truth.

**Required data fields:**
- `query`: The question/prompt
- `ground_truth`: Expected answer
- `context`: (optional) Additional context

### Dataset Mode
In dataset mode, you provide pre-existing responses that will be evaluated.

**Required data fields:**
- `query`: The question/prompt
- `response`: The actual response to evaluate
- `ground_truth`: Expected answer
- `context`: (optional) Additional context

## Built-in Evaluators

Available built-in evaluators:
- `builtin.relevance` - Measures response relevance to query
- `builtin.coherence` - Measures response coherence
- `builtin.fluency` - Measures response fluency
- `builtin.groundedness` - Measures if response is grounded in context
- `builtin.similarity` - Measures similarity to ground truth

## Custom Evaluators

Custom evaluators can be defined and deployed using:
```bash
python scripts/deployment/deploy-evaluators.py
```

Note: Custom evaluators are not yet fully integrated with the cloud evaluation API.

## Command-Line Usage

You can also run evaluations without a config file:

```bash
# Agent mode
python scripts/runtime/run_evaluation.py \
  --mode agent \
  --agent hello-world \
  --data evaluations/evaluation-data/data-sample.jsonl \
  --evaluators builtin.relevance builtin.coherence \
  --output evaluations/results/my-eval.json

# Dataset mode
python scripts/runtime/run_evaluation.py \
  --mode dataset \
  --data evaluations/evaluation-data/data-sample.jsonl \
  --evaluators builtin.relevance builtin.coherence \
  --output evaluations/results/my-eval.json
```

## Environment

Use `--environment` to specify the target environment:
```bash
python scripts/runtime/run_evaluation.py --config my-eval.yaml --environment prod
```

Available environments: `dev`, `test`, `prod`
