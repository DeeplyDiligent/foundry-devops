# Microsoft Azure AI Foundry CI/CD Repository

This repository contains the infrastructure and configuration for deploying AI agents and workflows to Microsoft Azure AI Foundry across multiple environments.

## 🏗️ Repository Structure

```
foundry-devops/
├── .github/
│   └── workflows/          # GitHub Actions CI/CD pipelines
│       ├── deploy-dev.yml
│       ├── deploy-test.yml
│       └── deploy-prod.yml
├── agents/                 # Agent definitions (YAML)
│   ├── hello-world.yaml
│   ├── customer-support.yaml
│   └── data-analyst.yaml
├── workflows/              # Workflow definitions (YAML)
│   ├── hello-world-workflow.yaml
│   ├── customer-support-workflow.yaml
│   └── data-analysis-workflow.yaml
├── evaluations/            # Testing and evaluation
│   ├── config.yaml
│   ├── test-cases/
│   │   ├── hello-world-tests.yaml
│   │   ├── customer-support-tests.yaml
│   │   └── data-analyst-tests.yaml
│   └── results/            # Test results (generated)
├── scripts/                # Deployment and utility scripts
│   ├── deployment/deploy-agents-and-workflows.py
│   ├── validate_yamls.py
│   └── run_evaluations.py
├── config/                 # Configuration files
│   └── environments.yaml
└── requirements.txt
```

## 🚀 Getting Started

### Prerequisites

- Python 3.11+
- Azure CLI
- GitHub repository with appropriate secrets configured

### Local Setup

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd foundry-devops
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Azure credentials:**
   ```bash
   az login
   ```

4. **Set environment variables:**
   ```bash
   export AZURE_SUBSCRIPTION_ID="your-subscription-id"
   export AZURE_TENANT_ID="your-tenant-id"
   export AZURE_CLIENT_ID="your-client-id"
   ```

## 🔄 CI/CD Pipeline

### Branch Strategy

- `dev` → Development environment
- `test` → Test/Staging environment
- `main/prod` → Production environment

### Deployment Flow

1. **Validation** - Validates YAML syntax and structure
2. **Deployment** - Deploys agents and workflows
3. **Evaluation** - Runs automated tests
4. **Reporting** - Generates evaluation reports

### GitHub Actions Workflows

Each environment has its own workflow that triggers on push to the respective branch:

- **deploy-dev.yml** - Deploys to dev on push to `dev` branch
- **deploy-test.yml** - Deploys to test on push to `test` branch
- **deploy-prod.yml** - Deploys to prod on push to `main/prod` branch

## 🔐 Required GitHub Secrets

Configure these secrets in your GitHub repository:

### Required for all environments:
- `AZURE_SUBSCRIPTION_ID` - Your Azure subscription ID
- `AZURE_TENANT_ID` - Your Azure tenant ID
- `AZURE_CLIENT_ID` - Service principal client ID

### Environment-specific:
- `DEV_AZURE_PROJECT_ENDPOINT` - Dev project endpoint
- `TEST_AZURE_PROJECT_ENDPOINT` - Test project endpoint
- `PROD_AZURE_PROJECT_ENDPOINT` - Prod project endpoint

## 📝 Creating Agents

Create a new agent by adding a YAML file to the `agents/` directory:

```yaml
metadata:
  logo: Avatar_Default.svg
  description: "Your agent description"
  modified_at: "1736294400"
object: agent.version
id: your-agent:1
name: your-agent
version: "1"
description: "Agent description"
created_at: 1736294400
definition:
  kind: prompt
  model: gpt-4.1
  instructions: |
    Your agent instructions here
  tools: []
```

## 📊 Creating Workflows

Create a new workflow by adding a YAML file to the `workflows/` directory:

```yaml
kind: workflow
trigger:
  kind: OnConversationStart
  id: trigger_id
  actions:
    - kind: InvokeAzureAgent
      id: action_id
      conversationId: =System.ConversationId
      agent:
        name: your-agent
      input:
        messages: =System.LastMessage
      output:
        autoSend: true
id: your-workflow
name: Your Workflow
description: "Workflow description"
```

## 🧪 Testing & Evaluations

### Running Tests Locally

```bash
# Validate YAML files
python scripts/validate_yamls.py --type all

# Run evaluations
python scripts/run_evaluations.py --environment dev --suite smoke
```

### Creating Test Cases

Add test cases to `evaluations/test-cases/`:

```yaml
test_cases:
  - id: test_1
    name: "Test Name"
    agent: agent-name
    input: "Test input"
    expected_contains:
      - "expected"
      - "phrases"
    metrics:
      - response_time
      - accuracy_score
```

### Evaluation Suites

- **smoke** - Quick smoke tests for basic functionality
- **full** - Comprehensive tests for all agents
- **regression** - Critical path regression testing

## 🛠️ Manual Deployment

### Deployment Scripts

Deploy agents, workflows, evaluators, and evaluation rules using these scripts:

```bash
# Activate virtual environment (if using one)
source venv/bin/activate

# Deploy agents and workflows
python scripts/deployment/deploy-agents-and-workflows.py --environment dev --type agents
python scripts/deployment/deploy-agents-and-workflows.py --environment dev --type workflows
python scripts/deployment/deploy-agents-and-workflows.py --environment dev --type all

# Deploy custom evaluators
python scripts/deployment/deploy-evaluators.py --environment dev

# Deploy evaluation rules (continuous evaluation)
# List all evaluation rules
python scripts/deployment/deploy-evaluation-rules.py --list

# Create a new evaluation rule for an agent
python scripts/deployment/deploy-evaluation-rules.py \
  --environment dev \
  --agent purple-workflow \
  --evaluators builtin.relevance builtin.coherence purple-checker

# Enable/disable evaluation rules
python scripts/deployment/deploy-evaluation-rules.py --enable continuous_evaluation_hello-world_2026-01-07
python scripts/deployment/deploy-evaluation-rules.py --disable continuous_evaluation_hello-world_2026-01-07

# Delete an evaluation rule
python scripts/deployment/deploy-evaluation-rules.py --delete continuous_evaluation_hello-world_2026-01-07

# Validate YAML files before deployment
python scripts/deployment/validate_yamls.py
```

### Runtime Scripts

Run evaluations and read conversation history:

```bash
# Run evaluation on an agent with a test dataset
python scripts/runtime/run_evaluation.py --config evaluations/evaluation-groups/purple-test.yaml

# Run evaluation with command-line arguments
python scripts/runtime/run_evaluation.py \
  --environment dev \
  --mode agent \
  --agent hello-world \
  --data evaluations/evaluation-data/purple-test.jsonl \
  --evaluators builtin.relevance builtin.coherence \
  --deployment gpt-4.1

# Read conversation history
python scripts/runtime/read_conversation.py \
  --environment dev \
  --conversation-id <conversation-id>
```

## 📈 Monitoring & Results

Evaluation results are automatically generated and stored in:
- `evaluations/results/dev/` - Dev results (7 day retention)
- `evaluations/results/test/` - Test results (30 day retention)
- `evaluations/results/prod/` - Prod results (90 day retention)

Results include:
- `results-{timestamp}.json` - Raw test results
- `summary-{timestamp}.md` - Markdown summary

## 🔧 Troubleshooting

### Validation Failures
Check YAML syntax and required fields using:
```bash
python scripts/validate_yamls.py --type all
```

### Deployment Failures
- Verify Azure credentials are correct
- Check endpoint URLs in `config/environments.yaml`
- Review GitHub Actions logs

### Evaluation Failures
- Check test case definitions
- Verify agents are deployed correctly
- Review threshold settings in `evaluations/config.yaml`

## 📚 Additional Resources

- [Azure AI Foundry Documentation](https://learn.microsoft.com/azure/ai-studio/)
- [Azure AI Projects SDK](https://learn.microsoft.com/python/api/overview/azure/ai-projects)
- [GitHub Actions Documentation](https://docs.github.com/actions)

## 🤝 Contributing

1. Create a feature branch from `dev`
2. Make your changes
3. Test locally using validation and evaluation scripts
4. Submit a pull request to `dev`
5. After approval, promote to `test` then `prod`

## 📄 License

[Your License Here]
