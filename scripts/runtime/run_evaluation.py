#!/usr/bin/env python3
"""
Run evaluations on agent responses or dataset responses using Azure AI Foundry.

Two modes:
1. Dataset mode: Evaluate responses from JSONL file (query, ground_truth, response)
2. Agent mode: Run agent to get responses for queries, then evaluate them (query, ground_truth only)

Configuration can be provided via YAML file or command-line arguments.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import yaml

from azure.identity.aio import DefaultAzureCredential
from azure.ai.projects.aio import AIProjectClient
from azure.ai.agents.aio import AgentsClient
from openai.types.evals.create_eval_jsonl_run_data_source_param import (
    CreateEvalJSONLRunDataSourceParam,
    SourceFileID,
)


def load_evaluation_config(config_path: Path) -> Dict[str, Any]:
    """Load evaluation configuration from YAML file."""
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    return config


def load_environment_config(environment: str) -> Dict[str, str]:
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


def load_jsonl(file_path: Path) -> List[Dict[str, Any]]:
    """Load JSONL file."""
    data = []
    with open(file_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(json.loads(line))
    return data


async def get_agent_response(agents_client: AgentsClient, agent_name: str, query: str) -> str:
    """Get response from an agent for a given query."""
    try:
        # Create a thread
        thread = await agents_client.threads.create()
        
        # Add user message
        await agents_client.threads.messages.create(
            thread_id=thread.id,
            role="user",
            content=query
        )
        
        # Run the agent
        run = await agents_client.threads.runs.create_and_process(
            thread_id=thread.id,
            agent_name=agent_name
        )
        
        # Get the response
        if run.status == "completed":
            messages = await agents_client.threads.messages.list(thread_id=thread.id)
            # Get the last assistant message
            async for message in messages:
                if message.role == "assistant":
                    # Extract text content
                    if message.content and len(message.content) > 0:
                        return message.content[0].text.value
        
        return f"Error: Agent run status = {run.status}"
        
    except Exception as e:
        return f"Error getting agent response: {e}"


async def run_evaluation_with_foundry(
    project_client: AIProjectClient,
    dataset_id: str,
    evaluators: List[Dict[str, Any]],
    display_name: str,
    model_deployment: str,
    agent_name: str = None,
    agent_version: str = None,
    data_items: List[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Run evaluation using Azure AI Foundry cloud evaluation API.
    
    Uses the OpenAI evals API through Foundry project client.
    Supports both dataset mode and agent target mode.
    """
    try:
        # Get OpenAI client from project client
        openai_client = project_client.get_openai_client()
        
        # Determine if this is agent mode
        is_agent_mode = agent_name is not None
        
        # Define data source config
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
        for evaluator in evaluators:
            eval_name = evaluator["name"]
            eval_version = evaluator.get("version", "")
            eval_type = evaluator.get("type", "builtin")
            params = evaluator.get("parameters", {})
            
            # Check if it's a built-in evaluator or custom
            if eval_type == "builtin" or eval_name.startswith("builtin."):
                # Built-in evaluator
                criterion = {
                    "type": "azure_ai_evaluator",
                    "name": eval_name.replace("builtin.", ""),
                    "evaluator_name": eval_name,
                    "initialization_parameters": {
                        "deployment_name": params.get("deployment_name", model_deployment)
                    },
                }
                
                # Add data mapping for agent mode
                if is_agent_mode:
                    criterion["data_mapping"] = {
                        "query": "{{item.query}}",
                        "response": "{{sample.output_text}}",
                        "context": "{{item.context}}",
                        "ground_truth": "{{item.ground_truth}}",
                    }
                
                testing_criteria.append(criterion)
            else:
                # Custom evaluator - use the same format as built-in
                criterion = {
                    "type": "azure_ai_evaluator",
                    "name": eval_name,
                    "evaluator_name": eval_name,  # Just the name, not name:version
                    "data_mapping": {
                        "query": "{{item.query}}",
                        "response": "{{item.response}}",
                        "context": "{{item.context}}",
                        "ground_truth": "{{item.ground_truth}}",
                    },
                    "initialization_parameters": {
                        "deployment_name": params.get("deployment_name", model_deployment),
                        "threshold": params.get("threshold", 0.7)
                    },
                }
                
                # Update data mapping for agent mode
                if is_agent_mode:
                    criterion["data_mapping"]["response"] = "{{sample.output_text}}"
                
                testing_criteria.append(criterion)
        
        print(f"\n📊 Creating evaluation: {display_name}")
        
        # Create eval group
        eval_object = await openai_client.evals.create(
            name=display_name,
            data_source_config=data_source_config,
            testing_criteria=testing_criteria,
        )
        
        print(f"   ✓ Evaluation created: {eval_object.id}")
        
        # Create eval run with dataset
        print(f"\n🚀 Starting evaluation run...")
        
        # Choose data source based on mode
        if agent_name and agent_version and data_items:
            # Agent target mode - Azure will call the agent for us
            data_source = {
                "type": "azure_ai_target_completions",
                "source": {
                    "type": "file_content",
                    "content": [
                        {"item": item} for item in data_items
                    ]
                },
                "input_messages": {
                    "type": "template",
                    "template": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": {
                                "type": "input_text",
                                "text": "{{item.query}}"
                            }
                        }
                    ]
                },
                "target": {
                    "type": "azure_ai_agent",
                    "name": agent_name,
                    "version": agent_version
                }
            }
        else:
            # Dataset mode - use pre-existing responses
            data_source = CreateEvalJSONLRunDataSourceParam(
                type="jsonl",
                source=SourceFileID(
                    type="file_id",
                    id=dataset_id,
                ),
            )
        
        eval_run = await openai_client.evals.runs.create(
            eval_id=eval_object.id,
            name=f"{display_name} - Run",
            metadata={
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "mode": "agent_target" if agent_name else "dataset"
            },
            data_source=data_source,
        )
        
        print(f"   ✓ Evaluation run created: {eval_run.id}")
        print(f"   Status: {eval_run.status}")
        
        # Poll until complete
        print(f"\n⏳ Waiting for evaluation to complete...")
        while True:
            run = await openai_client.evals.runs.retrieve(
                run_id=eval_run.id,
                eval_id=eval_object.id
            )
            
            if run.status in ("completed", "failed", "cancelled"):
                break
            
            print(f"   Status: {run.status}...")
            await asyncio.sleep(5)
        
        print(f"\n✅ Evaluation completed!")
        print(f"   Final status: {run.status}")
        
        # Get output items if completed
        output_items = []
        if run.status == "completed":
            print(f"\n📋 Fetching results...")
            async for item in openai_client.evals.runs.output_items.list(
                run_id=run.id,
                eval_id=eval_object.id
            ):
                # Convert to dict properly, handling nested objects
                item_dict = item.model_dump() if hasattr(item, 'model_dump') else dict(item)
                output_items.append(item_dict)
            
            if hasattr(run, 'report_url') and run.report_url:
                print(f"\n🔗 View full report: {run.report_url}")
        
        return {
            "eval_id": eval_object.id,
            "run_id": run.id,
            "status": run.status,
            "report_url": getattr(run, 'report_url', None),
            "output_items": output_items[:10]  # First 10 results
        }
        
    except Exception as e:
        print(f"\n❌ Evaluation failed: {e}")
        import traceback
        traceback.print_exc()
        return {
            "status": "error",
            "error": str(e)
        }


async def main():
    parser = argparse.ArgumentParser(
        description="Run evaluations on agent responses or dataset responses"
    )
    parser.add_argument(
        "--config",
        type=str,
        help="Path to YAML configuration file"
    )
    parser.add_argument(
        "--environment",
        choices=["dev", "test", "prod"],
        default="dev",
        help="Target environment (default: dev)"
    )
    
    # Legacy command-line arguments (optional, overrides config file)
    parser.add_argument(
        "--mode",
        choices=["dataset", "agent"],
        help="Evaluation mode: 'dataset' (evaluate existing responses) or 'agent' (get agent responses first)"
    )
    parser.add_argument(
        "--data",
        type=str,
        help="Path to JSONL file with test data"
    )
    parser.add_argument(
        "--agent",
        type=str,
        help="Agent name (required for agent mode)"
    )
    parser.add_argument(
        "--evaluators",
        type=str,
        nargs="+",
        help="List of evaluator names to run"
    )
    parser.add_argument(
        "--deployment",
        type=str,
        default="gpt-4.1",
        help="Model deployment name for evaluators (default: gpt-4.1)"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Path to save evaluation results (JSON)"
    )
    
    args = parser.parse_args()
    
    # Load configuration
    if args.config:
        config_path = Path(args.config)
        eval_config = load_evaluation_config(config_path)
        
        # Extract values from config
        mode = eval_config.get('mode', 'dataset')
        data_file = eval_config.get('data', {}).get('file')
        agent_name = eval_config.get('agent', {}).get('name') if mode == 'agent' else None
        evaluators_config = eval_config.get('evaluators', [])
        output_dir = eval_config.get('output', {}).get('directory', 'evaluations/results')
        eval_name = eval_config.get('name', 'evaluation')
        
        # Convert evaluators from config format
        evaluator_names = []
        evaluator_params = {}
        for evaluator in evaluators_config:
            eval_name_str = evaluator['name']
            evaluator_names.append(eval_name_str)
            evaluator_params[eval_name_str] = evaluator.get('parameters', {})
        
    else:
        # Use command-line arguments
        if not args.mode or not args.data:
            parser.error("--mode and --data are required when not using --config")
        
        mode = args.mode
        data_file = args.data
        agent_name = args.agent
        evaluator_names = args.evaluators or ["builtin.relevance", "builtin.coherence"]
        evaluator_params = {name: {"deployment_name": args.deployment, "threshold": 0.7} for name in evaluator_names}
        output_dir = None
        eval_name = "evaluation"
    
    # Validate arguments
    if mode == "agent" and not agent_name:
        parser.error("agent name is required when mode is 'agent'")
    
    print("="*80)
    print("🎯 Azure AI Foundry - Evaluation Runner")
    print("="*80)
    print(f"\nMode: {mode}")
    print(f"Environment: {args.environment}")
    print(f"Data file: {data_file}")
    if agent_name:
        print(f"Agent: {agent_name}")
    print(f"Evaluators: {', '.join(evaluator_names)}")
    
    try:
        # Load configuration
        env_config = load_environment_config(args.environment)
        
        # Load data
        data_path = Path(data_file)
        if not data_path.exists():
            print(f"\n❌ Error: Data file not found: {data_path}")
            return 1
        
        print(f"\n📁 Loading data from {data_path.name}...")
        data = load_jsonl(data_path)
        print(f"   Loaded {len(data)} data point(s)")
        
        # Validate data format
        if mode == "dataset":
            # Check for required fields: query, ground_truth, response
            for i, item in enumerate(data):
                if "query" not in item or "response" not in item:
                    print(f"\n❌ Error: Data point {i} missing 'query' or 'response' field")
                    return 1
        else:  # agent mode
            # Check for required fields: query, ground_truth
            for i, item in enumerate(data):
                if "query" not in item:
                    print(f"\n❌ Error: Data point {i} missing 'query' field")
                    return 1
        
        # Initialize Azure clients
        print("\n🔐 Authenticating with Azure...")
        async with DefaultAzureCredential() as credential:
            async with AIProjectClient(
                endpoint=env_config['endpoint'],
                credential=credential
            ) as project_client:
                print("  ✓ Connected to Azure AI Foundry")
                
                # Agent mode: Get agent responses first
                if mode == "agent":
                    print(f"\n🤖 Looking up agent '{agent_name}'...")
                    
                    # Get agent version
                    agent_version = None
                    pager = project_client.agents.list_versions(agent_name=agent_name)
                    async for ver in pager:
                        agent_version = ver.version if hasattr(ver, 'version') else ver.get('version')
                        break
                    
                    if not agent_version:
                        print(f"\n❌ Agent '{agent_name}' not found")
                        return 1
                    
                    print(f"   ✓ Found agent: {agent_name} (v{agent_version})")
                    
                    # Prepare data items for agent target evaluation
                    # Azure will call the agent for us, we just need query and ground_truth
                    data_items = []
                    for item in data:
                        eval_item = {
                            "query": item["query"],
                            "ground_truth": item.get("ground_truth", ""),
                        }
                        if "context" in item:
                            eval_item["context"] = item["context"]
                        data_items.append(eval_item)
                
                # Get evaluator versions
                print("\n🔍 Looking up evaluators...")
                evaluators_list = []
                for eval_name_str in evaluator_names:
                    try:
                        # Get parameters for this evaluator
                        params = evaluator_params.get(eval_name_str, {})
                        deployment = params.get("deployment_name", args.deployment)
                        threshold = params.get("threshold", 0.7)
                        
                        if eval_name_str.startswith("builtin."):
                            # Built-in evaluator - get version
                            pager = project_client.evaluators.list_versions(name=eval_name_str)
                            latest = None
                            async for ver in pager:
                                latest = ver
                                break
                            
                            if latest:
                                version = latest.version if hasattr(latest, 'version') else latest.get('version')
                                evaluators_list.append({
                                    "name": eval_name_str,
                                    "version": version,
                                    "type": "builtin",
                                    "parameters": {
                                        "deployment_name": deployment,
                                        "threshold": threshold
                                    }
                                })
                                print(f"   ✓ {eval_name_str} (v{version})")
                            else:
                                print(f"   ⚠️  {eval_name_str} not found, skipping")
                        else:
                            # Custom evaluator - get version
                            pager = project_client.evaluators.list_versions(name=eval_name_str)
                            latest = None
                            async for ver in pager:
                                latest = ver
                                break
                            
                            if latest:
                                version = latest.version if hasattr(latest, 'version') else latest.get('version')
                                evaluators_list.append({
                                    "name": eval_name_str,
                                    "version": version,
                                    "type": "custom",
                                    "parameters": {
                                        "deployment_name": deployment,
                                        "threshold": threshold
                                    }
                                })
                                print(f"   ✓ {eval_name_str} (v{version})")
                            else:
                                print(f"   ⚠️  {eval_name_str} not found, skipping")
                    except Exception as e:
                        print(f"   ✗ Error checking {eval_name_str}: {e}")
                
                if not evaluators_list:
                    print("\n❌ No valid evaluators found")
                    return 1
                
                # Run evaluation based on mode
                if mode == "agent":
                    # Agent target mode - no dataset upload needed, Azure will call agent
                    print(f"\n📊 Running agent target evaluation...")
                    results = await run_evaluation_with_foundry(
                        project_client,
                        dataset_id=None,
                        evaluators=evaluators_list,
                        display_name=f"Evaluation - agent {agent_name} - {data_path.stem}",
                        model_deployment=args.deployment,
                        agent_name=agent_name,
                        agent_version=agent_version,
                        data_items=data_items
                    )
                    dataset_name = None
                else:
                    # Dataset mode - upload dataset with responses
                    print(f"\n📤 Uploading dataset to Azure AI Foundry...")
                    dataset_name = f"eval-data-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
                    dataset = await project_client.datasets.upload_file(
                        name=dataset_name,
                        version="1",
                        file_path=str(data_path),
                    )
                    print(f"   ✓ Dataset uploaded: {dataset.id}")
                    print(f"   Name: {dataset_name}")
                    
                    # Run evaluation
                    display_name = f"Evaluation - dataset mode - {data_path.stem}"
                    results = await run_evaluation_with_foundry(
                        project_client,
                        dataset.id,
                        evaluators_list,
                        display_name,
                        args.deployment
                    )
                
                # Save results (only if explicitly requested)
                if args.output:
                    output_path = Path(args.output)
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_data = {
                        "evaluation": results,
                        "config": {
                            "mode": mode,
                            "agent": agent_name,
                            "data_file": str(data_path),
                            "dataset_name": dataset_name,
                            "evaluators": evaluators_list
                        }
                    }
                    with open(output_path, 'w') as f:
                        json.dump(output_data, f, indent=2)
                    print(f"\n💾 Results saved to: {output_path}")
                
                # Display summary
                print("\n" + "="*80)
                print("📊 Evaluation Summary")
                print("="*80)
                print(f"Status: {results.get('status', 'unknown')}")
                print(f"Evaluation ID: {results.get('eval_id', 'N/A')}")
                print(f"Run ID: {results.get('run_id', 'N/A')}")
                if results.get('report_url'):
                    print(f"Report URL: {results['report_url']}")
                print("="*80)
                
                if results.get('status') == 'completed':
                    print("\n✅ Evaluation completed successfully!")
                    return 0
                else:
                    print(f"\n⚠️  Evaluation ended with status: {results.get('status')}")
                    return 1
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
