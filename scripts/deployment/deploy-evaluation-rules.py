#!/usr/bin/env python3
"""
Deploy automated evaluation rules to Azure AI Foundry.

Creates rules that automatically trigger evaluations when agents complete responses.
"""
import argparse
import asyncio
import sys
from pathlib import Path
from typing import List, Optional
import warnings
import yaml

from azure.identity.aio import DefaultAzureCredential
from azure.ai.projects.aio import AIProjectClient

# Suppress Pydantic serialization warnings from Azure SDK
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic.main")


def load_environment_config(environment: str) -> dict:
    """Load environment configuration from config/environments.yaml."""
    config_path = Path(__file__).parent.parent.parent / "config" / "environments.yaml"
    
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    if environment not in config:
        raise ValueError(f"Environment '{environment}' not found in configuration")
    
    env_config = config[environment]
    return {
        'endpoint': env_config['azure_project_endpoint']
    }


async def list_evaluation_rules(project_client: AIProjectClient):
    """List all evaluation rules."""
    print("\n📋 Listing evaluation rules...")
    
    try:
        # Get OpenAI client to fetch eval group details
        openai_client = project_client.get_openai_client()
        
        rules = []
        async for rule in project_client.evaluation_rules.list():
            rules.append(rule)
            
        if not rules:
            print("   No evaluation rules found")
            return []
        
        for rule in rules:
            rule_id = rule.id if hasattr(rule, 'id') else rule.get('id')
            enabled = rule.enabled if hasattr(rule, 'enabled') else rule.get('enabled')
            status = "✓ Enabled" if enabled else "✗ Disabled"
            
            # Get rule details
            rule_dict = rule.as_dict() if hasattr(rule, 'as_dict') else dict(rule)
            action = rule_dict.get('action', {})
            filter_dict = rule_dict.get('filter', {})
            
            eval_id = action.get('evalId', '')
            agent_filter = filter_dict.get('agentName', 'all agents')
            
            print(f"\n   ID: {rule_id}")
            print(f"   Status: {status}")
            print(f"   Agent: {agent_filter}")
            
            # Fetch evaluators from eval group
            if eval_id:
                try:
                    eval_group = await openai_client.evals.retrieve(eval_id)
                    eval_dict = eval_group.model_dump() if hasattr(eval_group, 'model_dump') else dict(eval_group)
                    
                    # Get testing criteria (use snake_case as that's what the API returns)
                    testing_criteria = eval_dict.get('testing_criteria', [])
                    
                    evaluators = []
                    for criterion in testing_criteria:
                        # The evaluator_name field contains the evaluator identifier
                        if isinstance(criterion, dict):
                            evaluator_name = criterion.get('evaluator_name', '')
                        else:
                            evaluator_name = getattr(criterion, 'evaluator_name', '')
                        
                        if evaluator_name:
                            evaluators.append(evaluator_name)
                    
                    print(f"   Eval Group: {eval_id}")
                    print(f"   Evaluators: {', '.join(evaluators) if evaluators else 'none'}")
                except Exception as e:
                    print(f"   Eval Group: {eval_id} (error fetching: {str(e)[:50]})")
        
        return rules
    except Exception as e:
        print(f"\n❌ Error listing rules: {e}")
        return []


async def create_evaluation_rule(
    project_client: AIProjectClient,
    agent_name: str,
    evaluators: List[str],
    enabled: bool = True,
    max_hourly_runs: int = 20
):
    """Create an evaluation rule for an agent."""
    print(f"\n🔧 Creating evaluation rule for agent '{agent_name}'...")
    
    try:
        # First, we need to create an eval group with the evaluators
        print(f"   Creating evaluation group...")
        
        # Get OpenAI client
        openai_client = project_client.get_openai_client()
        
        # Create data source config
        data_source_config = {
            "type": "custom",
            "item_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "response": {"type": "string"},
                    "context": {"type": "string"},
                    "ground_truth": {"type": "string"},
                },
                "required": [],
            },
            "include_sample_schema": True,
        }
        
        # Build testing criteria from evaluators
        testing_criteria = []
        for evaluator_name in evaluators:
            if evaluator_name.startswith("builtin."):
                testing_criteria.append({
                    "type": "azure_ai_evaluator",
                    "name": evaluator_name.replace("builtin.", ""),
                    "evaluator_name": evaluator_name,
                    "initialization_parameters": {
                        "deployment_name": "gpt-4.1"
                    },
                })
            else:
                # Custom evaluator
                testing_criteria.append({
                    "type": "azure_ai_evaluator",
                    "name": evaluator_name,
                    "evaluator_name": evaluator_name,
                    "data_mapping": {
                        "query": "{{item.query}}",
                        "response": "{{item.response}}",
                        "context": "{{item.context}}",
                        "ground_truth": "{{item.ground_truth}}",
                    },
                    "initialization_parameters": {
                        "deployment_name": "gpt-4.1",
                        "threshold": 0.7
                    },
                })
        
        # Create eval group
        eval_group = await openai_client.evals.create(
            name=f"Continuous Evaluation - {agent_name}",
            data_source_config=data_source_config,
            testing_criteria=testing_criteria,
        )
        
        print(f"   ✓ Evaluation group created: {eval_group.id}")
        
        # Create the rule configuration
        rule_config = {
            "displayName": f"Continuous Evaluation - {agent_name}",
            "description": f"Continuous evaluation rule for monitoring {agent_name} agent responses",
            "eventType": "responseCompleted",
            "filter": {
                "agentName": agent_name
            },
            "action": {
                "type": "continuousEvaluation",
                "evalId": eval_group.id,
                "maxHourlyRuns": max_hourly_runs
            },
            "enabled": enabled
        }
        
        print(f"   Event: responseCompleted")
        print(f"   Agent: {agent_name}")
        print(f"   Evaluators: {', '.join(evaluators)}")
        print(f"   Max hourly runs: {max_hourly_runs}")
        print(f"   Enabled: {enabled}")
        
        # Create the rule using create_or_update with proper arguments
        from datetime import datetime
        rule_name = f"continuous_evaluation_{agent_name}_{datetime.now().strftime('%Y-%m-%d')}"
        rule = await project_client.evaluation_rules.create_or_update(
            id=rule_name,
            evaluation_rule=rule_config
        )
        
        rule_id = rule.id if hasattr(rule, 'id') else rule.get('id', rule_name)
        print(f"\n✅ Rule created successfully!")
        print(f"   Rule ID: {rule_id}")
        
        return rule
    except Exception as e:
        print(f"\n❌ Error creating rule: {e}")
        import traceback
        traceback.print_exc()
        return None


async def delete_evaluation_rule(project_client: AIProjectClient, rule_id: str):
    """Delete an evaluation rule."""
    print(f"\n🗑️  Deleting evaluation rule: {rule_id}")
    
    try:
        await project_client.evaluation_rules.delete(id=rule_id)
        print(f"   ✓ Rule deleted successfully")
        return True
    except Exception as e:
        print(f"\n❌ Error deleting rule: {e}")
        return False


async def update_evaluation_rule(
    project_client: AIProjectClient,
    rule_id: str,
    enabled: Optional[bool] = None
):
    """Update an evaluation rule."""
    print(f"\n✏️  Updating evaluation rule: {rule_id}")
    
    try:
        # Get existing rule
        rule = await project_client.evaluation_rules.get(id=rule_id)
        rule_dict = rule.as_dict() if hasattr(rule, 'as_dict') else dict(rule)
        
        # Update fields
        if enabled is not None:
            rule_dict['enabled'] = enabled
            print(f"   Setting enabled: {enabled}")
        
        # Update the rule
        updated_rule = await project_client.evaluation_rules.create_or_update(
            id=rule_id,
            evaluation_rule=rule_dict
        )
        
        print(f"   ✓ Rule updated successfully")
        return updated_rule
    except Exception as e:
        print(f"\n❌ Error updating rule: {e}")
        return None


async def main():
    parser = argparse.ArgumentParser(
        description="Deploy automated evaluation rules for agents"
    )
    parser.add_argument(
        "--environment",
        choices=["dev", "test", "prod"],
        default="dev",
        help="Target environment (default: dev)"
    )
    parser.add_argument(
        "--agent",
        type=str,
        help="Agent name to create evaluation rule for"
    )
    parser.add_argument(
        "--evaluators",
        type=str,
        nargs="+",
        default=["builtin.relevance", "builtin.coherence", "builtin.groundedness"],
        help="List of evaluators to run (default: builtin.relevance builtin.coherence builtin.groundedness)"
    )
    parser.add_argument(
        "--enabled",
        action="store_true",
        default=True,
        help="Enable the rule immediately (default: True)"
    )
    parser.add_argument(
        "--disabled",
        action="store_true",
        help="Create rule in disabled state"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all evaluation rules"
    )
    parser.add_argument(
        "--delete",
        type=str,
        metavar="RULE_ID",
        help="Delete an evaluation rule by ID"
    )
    parser.add_argument(
        "--enable",
        type=str,
        metavar="RULE_ID",
        help="Enable an evaluation rule by ID"
    )
    parser.add_argument(
        "--disable",
        type=str,
        metavar="RULE_ID",
        help="Disable an evaluation rule by ID"
    )
    
    args = parser.parse_args()
    
    print("="*80)
    print("🎯 Azure AI Foundry - Automated Evaluation Rules")
    print("="*80)
    
    try:
        # Load configuration
        config = load_environment_config(args.environment)
        
        print(f"\n📋 Environment: {args.environment}")
        print(f"   Endpoint: {config['endpoint']}")
        
        # Initialize Azure client
        print("\n🔐 Authenticating with Azure...")
        async with DefaultAzureCredential() as credential:
            async with AIProjectClient(
                endpoint=config['endpoint'],
                credential=credential
            ) as project_client:
                print("  ✓ Connected to Azure AI Foundry")
                
                # Handle different operations
                if args.list:
                    await list_evaluation_rules(project_client)
                
                elif args.delete:
                    success = await delete_evaluation_rule(project_client, args.delete)
                    return 0 if success else 1
                
                elif args.enable:
                    await update_evaluation_rule(project_client, args.enable, enabled=True)
                
                elif args.disable:
                    await update_evaluation_rule(project_client, args.disable, enabled=False)
                
                elif args.agent:
                    enabled = not args.disabled
                    rule = await create_evaluation_rule(
                        project_client,
                        args.agent,
                        args.evaluators,
                        enabled=enabled
                    )
                    
                    if rule:
                        print("\n" + "="*80)
                        print("✅ Evaluation rule deployed successfully!")
                        print("="*80)
                        print(f"\nThe rule will automatically run evaluations when agent '{args.agent}'")
                        print("completes a response.")
                        return 0
                    else:
                        return 1
                else:
                    parser.error("Please specify --agent, --list, --delete, --enable, or --disable")
                
                return 0
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
