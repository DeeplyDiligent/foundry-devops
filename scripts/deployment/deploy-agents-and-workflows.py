#!/usr/bin/env python3
"""
Deployment script for Microsoft Foundry agents and workflows.
Creates persisted agents in Foundry using azure-ai-agents SDK.
"""
import argparse
import asyncio
import os
import sys
import yaml
from pathlib import Path
from typing import List, Dict, Any
from azure.identity.aio import DefaultAzureCredential
from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition, WorkflowAgentDefinition


def load_environments() -> Dict[str, Dict[str, str]]:
    """Load environment configurations from config/environments.yaml."""
    config_path = Path("config/environments.yaml")
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    # Convert to expected format with 'endpoint' key
    environments = {}
    for env_name, env_config in config.items():
        if isinstance(env_config, dict) and 'azure_project_endpoint' in env_config:
            environments[env_name] = {
                'endpoint': env_config['azure_project_endpoint']
            }
    
    return environments


# Load environment configurations from YAML file
ENVIRONMENTS = load_environments()


class FoundryDeployer:
    """Handles deployment of agents and workflows to Microsoft Foundry."""
    
    def __init__(self, environment: str):
        self.environment = environment
        self.config = ENVIRONMENTS.get(environment)
        if not self.config:
            raise ValueError(f"Unknown environment: {environment}")
        
        self.credential = None
        self.project_client = None
        print(f"✓ Configuration loaded for ({environment})")
        print(f"  Endpoint: {self.config['endpoint']}")
    
    async def __aenter__(self):
        """Async context manager entry."""
        self.credential = DefaultAzureCredential()
        self.project_client = AIProjectClient(
            endpoint=self.config['endpoint'],
            credential=self.credential
        )
        print(f"✓ Connected to Microsoft Foundry")
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        # Close project client
        if self.project_client:
            await self.project_client.close()
        # Close credential
        if self.credential:
            await self.credential.close()
    
    def load_yaml_files(self, directory: str) -> List[Dict[str, Any]]:
        """Load all YAML files from a directory."""
        yaml_files = []
        path = Path(directory)
        
        if not path.exists():
            print(f"⚠ Directory not found: {directory}")
            return yaml_files
        
        for file_path in path.glob("**/*.yaml"):
            try:
                with open(file_path, 'r') as f:
                    data = yaml.safe_load(f)
                    data['_file_path'] = str(file_path)
                    yaml_files.append(data)
                    print(f"  Loaded: {file_path.name}")
            except Exception as e:
                print(f"✗ Error loading {file_path}: {e}")
        
        return yaml_files
    
    async def deploy_agents(self) -> bool:
        """Deploy all agents to Microsoft Foundry - creates NEW Foundry agents (not classic)."""
        print(f"\n📤 Deploying NEW Foundry agents to {self.environment}...")
        
        agents = self.load_yaml_files("agents")
        if not agents:
            print("⚠ No agents found to deploy")
            return True
        
        success = True
        for agent_def in agents:
            try:
                agent_name = agent_def.get('name', 'unknown')
                print(f"  Deploying agent: {agent_name}")
                
                # Extract agent configuration from YAML
                definition = agent_def.get('definition', {})
                model = definition.get('model', 'gpt-4o')
                instructions = definition.get('instructions', '')
                description = agent_def.get('description', '')
                
                # Create NEW Foundry agent using create_version()
                # This creates a prompt-based agent that appears in the NEW Foundry UI
                agent = await self.project_client.agents.create_version(
                    agent_name=agent_name,
                    description=description,
                    definition=PromptAgentDefinition(
                        model=model,
                        instructions=instructions
                    )
                )
                
                print(f"  ✓ Agent '{agent_name}' created successfully (v{agent.version})")
                print(f"    Model: {model}")
                
            except Exception as e:
                print(f"  ✗ Failed to deploy agent '{agent_name}': {e}")
                import traceback
                traceback.print_exc()
                success = False
        
        return success
    
    async def deploy_workflows(self) -> bool:
        """Deploy all workflows to Microsoft Foundry - creates NEW Foundry workflow agents."""
        print(f"\n📤 Deploying NEW Foundry workflows to {self.environment}...")
        
        path = Path("workflows")
        if not path.exists():
            print("⚠ No workflows directory found")
            return True
        
        workflow_files = list(path.glob("*.yaml")) + list(path.glob("*.yml"))
        if not workflow_files:
            print("⚠ No workflows found to deploy")
            return True
        
        success = True
        for workflow_file in workflow_files:
            try:
                # Read the raw YAML file
                with open(workflow_file, 'r') as f:
                    workflow_yaml_string = f.read()
                
                # Also parse it to get metadata
                workflow_def = yaml.safe_load(workflow_yaml_string)
                
                # Get workflow name from the YAML or derive from filename
                # Agent names must be alphanumeric + hyphens, no spaces
                workflow_name = workflow_def.get('id') or workflow_def.get('name', workflow_file.stem)
                workflow_name = workflow_name.lower().replace(' ', '-').replace('_', '-')
                description = workflow_def.get('description', '')
                
                print(f"  Deploying workflow: {workflow_name}")
                
                # Create NEW Foundry workflow agent using create_version()
                # Workflows are a type of agent with kind="workflow"
                # Pass the raw YAML string as the workflow definition
                workflow_agent = await self.project_client.agents.create_version(
                    agent_name=workflow_name,
                    description=description,
                    definition=WorkflowAgentDefinition(
                        workflow=workflow_yaml_string
                    )
                )
                
                print(f"  ✓ Workflow '{workflow_name}' created successfully (v{workflow_agent.version})")
                
            except Exception as e:
                print(f"  ✗ Failed to deploy workflow '{workflow_name if 'workflow_name' in locals() else workflow_file.stem}': {e}")
                import traceback
                traceback.print_exc()
                success = False
        
        return success


async def async_main():
    parser = argparse.ArgumentParser(
        description="Deploy agents and workflows to Microsoft Foundry"
    )
    parser.add_argument(
        "--environment",
        choices=["dev", "test", "prod"],
        required=True,
        help="Target environment"
    )
    parser.add_argument(
        "--type",
        choices=["agents", "workflows", "all"],
        default="all",
        help="What to deploy"
    )
    
    args = parser.parse_args()
    
    try:
        async with FoundryDeployer(args.environment) as deployer:
            success = True
            if args.type in ["agents", "all"]:
                success = await deployer.deploy_agents() and success
            
            if args.type in ["workflows", "all"]:
                success = await deployer.deploy_workflows() and success
            
            if success:
                print(f"\n✓ Deployment to {args.environment} completed successfully!")
                return 0
            else:
                print(f"\n✗ Deployment to {args.environment} completed with errors")
                return 1
                
    except Exception as e:
        print(f"\n✗ Deployment failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


def main():
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
