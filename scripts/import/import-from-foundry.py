#!/usr/bin/env python3
"""
Import script for Microsoft Foundry agents and workflows.
Fetches existing agents and workflows from Foundry and saves them as YAML files.
"""
import argparse
import asyncio
import os
import sys
import yaml
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from azure.identity.aio import DefaultAzureCredential
from azure.ai.projects.aio import AIProjectClient


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


class FoundryImporter:
    """Handles importing agents and workflows from Microsoft Foundry."""
    
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
    
    def get_local_agent_names(self) -> set:
        """Get list of agent names already in the local agents/ directory."""
        agents_dir = Path("agents")
        if not agents_dir.exists():
            return set()
        
        local_agents = set()
        for file_path in agents_dir.glob("*.yaml"):
            try:
                with open(file_path, 'r') as f:
                    data = yaml.safe_load(f)
                    if 'name' in data:
                        local_agents.add(data['name'])
            except Exception as e:
                print(f"⚠ Error reading {file_path}: {e}")
        
        return local_agents
    
    def get_local_workflow_names(self) -> set:
        """Get list of workflow names already in the local workflows/ directory."""
        workflows_dir = Path("workflows")
        if not workflows_dir.exists():
            return set()
        
        local_workflows = set()
        for file_path in workflows_dir.glob("*.yaml"):
            try:
                with open(file_path, 'r') as f:
                    data = yaml.safe_load(f)
                    # Workflows might have 'id' or 'name' field
                    workflow_name = data.get('id') or data.get('name', file_path.stem)
                    local_workflows.add(workflow_name)
            except Exception as e:
                print(f"⚠ Error reading {file_path}: {e}")
        
        return local_workflows
    
    async def list_agents(self) -> List[Dict[str, Any]]:
        """List all agents in Foundry."""
        print(f"\n📋 Listing agents from {self.environment}...")
        
        try:
            agents = []
            async for agent in self.project_client.agents.list():
                # Get latest version info from versions dictionary
                latest = agent.versions.get('latest', {}) if hasattr(agent.versions, 'get') else {}
                version = latest.get('version', '1') if isinstance(latest, dict) else '1'
                description = latest.get('description', '') if isinstance(latest, dict) else ''
                
                agents.append({
                    'name': agent.name,
                    'latest_version': version,
                    'description': description,
                    'created_at': latest.get('created_at') if isinstance(latest, dict) else None,
                    'modified_at': None
                })
                print(f"  • {agent.name} (v{version})")
            
            return agents
        except Exception as e:
            print(f"✗ Failed to list agents: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    async def import_agent(self, agent_name: str, version: Optional[str] = None) -> bool:
        """Import a specific agent from Foundry and save as YAML."""
        try:
            print(f"  Importing agent: {agent_name}")
            
            # Get agent version - if no version specified, get latest
            if version:
                agent_version = await self.project_client.agents.get_version(
                    agent_name=agent_name,
                    version=version
                )
            else:
                # Get agent returns the full agent object with versions
                agent_obj = await self.project_client.agents.get(agent_name=agent_name)
                # Extract the latest version
                agent_version = agent_obj.versions.get('latest') if hasattr(agent_obj.versions, 'get') else None
                if not agent_version:
                    print(f"  ✗ No latest version found for agent '{agent_name}'")
                    return False
            
            # Extract agent details
            agent_data = {
                'metadata': {
                    'description': agent_version.description or '',
                    'modified_at': str(int(datetime.now().timestamp())),
                },
                'object': 'agent.version',
                'id': f"{agent_version.name}:{agent_version.version}",
                'name': agent_version.name,
                'version': str(agent_version.version),
                'description': agent_version.description or '',
                'created_at': agent_version.created_at or int(datetime.now().timestamp()),
                'definition': {}
            }
            
            # Handle different agent types
            if hasattr(agent_version.definition, 'kind'):
                definition = agent_version.definition
                
                if definition.kind == 'prompt':
                    agent_data['definition'] = {
                        'kind': 'prompt',
                        'model': getattr(definition, 'model', 'gpt-4o'),
                        'instructions': getattr(definition, 'instructions', ''),
                    }
                    
                    # Add tools if they exist
                    if hasattr(definition, 'tools') and definition.tools:
                        agent_data['definition']['tools'] = []
                        for tool in definition.tools:
                            # Tools can be different types - use as_dict() if available
                            if hasattr(tool, 'as_dict'):
                                agent_data['definition']['tools'].append(tool.as_dict())
                            elif hasattr(tool, 'type'):
                                # Fallback to basic dict with type
                                tool_dict = {'type'  : tool.type if hasattr(tool, 'type') else 'unknown'}
                                agent_data['definition']['tools'].append(tool_dict)
                            else:
                                # Skip unknown tool types
                                print(f"    ⚠ Skipping unknown tool type: {type(tool).__name__}")
                                continue
                
                elif definition.kind == 'workflow':
                    # This is a workflow agent - save in workflows directory instead
                    print(f"  ⚠ Agent '{agent_name}' is a workflow type, skipping agent import")
                    return True
            
            # Save to YAML file
            output_dir = Path("agents")
            output_dir.mkdir(exist_ok=True)
            
            output_file = output_dir / f"{agent_name}.yaml"
            with open(output_file, 'w') as f:
                yaml.dump(agent_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            
            print(f"  ✓ Agent '{agent_name}' imported successfully")
            print(f"    Saved to: {output_file}")
            print(f"    Version: {agent_version.version}")
            
            return True
            
        except Exception as e:
            print(f"  ✗ Failed to import agent '{agent_name}': {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def import_workflow(self, workflow_name: str, version: Optional[str] = None) -> bool:
        """Import a specific workflow from Foundry and save as YAML."""
        try:
            print(f"  Importing workflow: {workflow_name}")
            
            # Get workflow agent version
            if version:
                workflow_version = await self.project_client.agents.get_version(
                    agent_name=workflow_name,
                    version=version,
                    headers={"Foundry-Features": "WorkflowAgents=V1Preview"}
                )
            else:
                # Get the full workflow agent object
                workflow_obj = await self.project_client.agents.get(
                    agent_name=workflow_name,
                    headers={"Foundry-Features": "WorkflowAgents=V1Preview"}
                )
                # Extract the latest version
                workflow_version = workflow_obj.versions.get('latest') if hasattr(workflow_obj.versions, 'get') else None
                if not workflow_version:
                    print(f"  ✗ No latest version found for workflow '{workflow_name}'")
                    return False
            
            # Check if this is actually a workflow
            if not hasattr(workflow_version.definition, 'kind') or workflow_version.definition.kind != 'workflow':
                print(f"  ⚠ Agent '{workflow_name}' is not a workflow type, skipping")
                return False
            
            # Get the workflow YAML string
            workflow_yaml = workflow_version.definition.workflow
            
            # Save to YAML file in workflows directory
            output_dir = Path("workflows")
            output_dir.mkdir(exist_ok=True)
            
            output_file = output_dir / f"{workflow_name}.yaml"
            with open(output_file, 'w') as f:
                f.write(workflow_yaml)
            
            print(f"  ✓ Workflow '{workflow_name}' imported successfully")
            print(f"    Saved to: {output_file}")
            print(f"    Version: {workflow_version.version}")
            
            return True
            
        except Exception as e:
            print(f"  ✗ Failed to import workflow '{workflow_name}': {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def import_all_new_agents(self, skip_existing: bool = True) -> bool:
        """Import all agents that don't exist locally."""
        print(f"\n📥 Importing new agents from {self.environment}...")
        
        # Get local agents
        local_agents = self.get_local_agent_names()
        print(f"  Found {len(local_agents)} existing local agents")
        
        # List remote agents
        remote_agents = await self.list_agents()
        
        if not remote_agents:
            print("  No agents found in Foundry")
            return True
        
        # Filter to only new agents
        new_agents = []
        for agent in remote_agents:
            if not skip_existing or agent['name'] not in local_agents:
                new_agents.append(agent)
        
        if not new_agents:
            print(f"\n✓ All agents are already imported locally")
            return True
        
        print(f"\n  Found {len(new_agents)} new agents to import:")
        for agent in new_agents:
            print(f"    • {agent['name']}")
        
        # Import each new agent
        success = True
        for agent in new_agents:
            result = await self.import_agent(agent['name'])
            success = success and result
        
        return success
    
    async def import_all_new_workflows(self, skip_existing: bool = True) -> bool:
        """Import all workflows that don't exist locally."""
        print(f"\n📥 Importing new workflows from {self.environment}...")
        
        # Get local workflows
        local_workflows = self.get_local_workflow_names()
        print(f"  Found {len(local_workflows)} existing local workflows")
        
        # List all agents (workflows are a type of agent)
        all_agents = await self.list_agents()
        
        # Filter to only workflow agents
        new_workflows = []
        for agent in all_agents:
            agent_name = agent['name']
            
            # Skip if already exists locally
            if skip_existing and agent_name in local_workflows:
                continue
            
            # Check if this is a workflow agent
            try:
                agent_obj = await self.project_client.agents.get(
                    agent_name=agent_name,
                    headers={"Foundry-Features": "WorkflowAgents=V1Preview"}
                )
                
                # Get the latest version to check its definition
                latest = agent_obj.versions.get('latest') if hasattr(agent_obj.versions, 'get') else None
                if latest and hasattr(latest.definition, 'kind') and latest.definition.kind == 'workflow':
                    new_workflows.append(agent_name)
            except Exception as e:
                # Not a workflow, skip
                continue
        
        if not new_workflows:
            print(f"\n✓ All workflows are already imported locally")
            return True
        
        print(f"\n  Found {len(new_workflows)} new workflows to import:")
        for workflow in new_workflows:
            print(f"    • {workflow}")
        
        # Import each new workflow
        success = True
        for workflow_name in new_workflows:
            result = await self.import_workflow(workflow_name)
            success = success and result
        
        return success


async def async_main():
    parser = argparse.ArgumentParser(
        description="Import agents and workflows from Microsoft Foundry"
    )
    parser.add_argument(
        "--environment",
        "-e",
        choices=list(ENVIRONMENTS.keys()),
        required=True,
        help="Target environment (e.g., dev, test, prod)"
    )
    parser.add_argument(
        "--type",
        "-t",
        choices=["agents", "workflows", "all"],
        default="all",
        help="What to import: agents, workflows, or all"
    )
    parser.add_argument(
        "--name",
        "-n",
        help="Import a specific agent/workflow by name"
    )
    parser.add_argument(
        "--version",
        "-v",
        help="Specific version to import (defaults to latest)"
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing local files"
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Only list available agents/workflows without importing"
    )
    
    args = parser.parse_args()
    
    try:
        async with FoundryImporter(args.environment) as importer:
            
            if args.list_only:
                # Just list what's available
                await importer.list_agents()
                return 0
            
            success = True
            
            if args.name:
                # Import specific agent or workflow
                if args.type in ["agents", "all"]:
                    success = await importer.import_agent(args.name, args.version)
                if args.type in ["workflows", "all"] and success:
                    success = await importer.import_workflow(args.name, args.version)
            else:
                # Import all new agents/workflows
                if args.type in ["agents", "all"]:
                    success = await importer.import_all_new_agents(skip_existing=not args.overwrite)
                if args.type in ["workflows", "all"] and success:
                    success = await importer.import_all_new_workflows(skip_existing=not args.overwrite)
            
            if success:
                print(f"\n✓ Import completed successfully")
                return 0
            else:
                print(f"\n✗ Import completed with errors")
                return 1
                
    except Exception as e:
        print(f"\n✗ Import failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


def main():
    """Entry point for the script."""
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
