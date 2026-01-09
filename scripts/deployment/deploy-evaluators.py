#!/usr/bin/env python3
"""
Script to deploy custom evaluators to Azure AI Foundry.
Reads evaluator definitions from JSON files and deploys them using Azure AI Projects SDK.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Dict, List, Any

from azure.identity.aio import DefaultAzureCredential
from azure.ai.projects.aio import AIProjectClient
import yaml


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


def load_evaluator_definitions(evaluators_dir: Path) -> List[Dict[str, Any]]:
    """Load all evaluator JSON definitions from a directory."""
    evaluators = []
    
    if not evaluators_dir.exists():
        raise FileNotFoundError(f"Evaluators directory not found: {evaluators_dir}")
    
    json_files = list(evaluators_dir.glob("*.json"))
    
    if not json_files:
        print(f"⚠️  No JSON files found in {evaluators_dir}")
        return evaluators
    
    print(f"\n📁 Found {len(json_files)} evaluator definition(s)")
    
    for json_file in json_files:
        try:
            with open(json_file, 'r') as f:
                evaluator = json.load(f)
                evaluators.append({
                    'file': json_file.name,
                    'definition': evaluator
                })
                print(f"  ✓ Loaded: {json_file.name}")
        except Exception as e:
            print(f"  ✗ Error loading {json_file.name}: {e}")
    
    return evaluators


def normalize_for_comparison(obj: Any) -> Any:
    """Normalize objects for comparison by removing read-only fields and converting models to dicts."""
    # Convert model objects to dicts
    if hasattr(obj, 'as_dict'):
        obj = obj.as_dict()
    elif hasattr(obj, '__dict__') and not isinstance(obj, (str, int, float, bool, type(None))):
        obj = dict(obj)
    
    if isinstance(obj, dict):
        # Remove read-only fields, auto-generated fields, and empty metadata
        exclude_keys = {'created_at', 'modified_at', 'created_by', 'id', 'version', 'name', 'data_schema', 'tags'}
        result = {}
        for k, v in obj.items():
            if k in exclude_keys:
                continue
            # Skip empty metadata
            if k == 'metadata' and (v is None or v == {}):
                continue
            result[k] = normalize_for_comparison(v)
        return result
    elif isinstance(obj, list):
        return [normalize_for_comparison(item) for item in obj]
    else:
        return obj


async def deploy_evaluator(
    client: AIProjectClient,
    evaluator_def: Dict[str, Any],
    dry_run: bool = False,
    force: bool = False
) -> tuple[bool, str]:
    """
    Deploy a single evaluator to Azure AI Foundry.
    Only creates a new version if the definition has changed.
    
    Args:
        client: AIProjectClient instance
        evaluator_def: Evaluator definition dictionary
        dry_run: If True, only validate without deploying
        force: If True, always create new version even if unchanged
        
    Returns:
        Tuple of (success: bool, status: str) where status is 'deployed', 'skipped', or 'failed'
    """
    evaluator_name = evaluator_def.get('name', 'unknown')
    
    try:
        if dry_run:
            print(f"  [DRY RUN] Would deploy: {evaluator_name}")
            return True, 'deployed'
        
        # Extract name from definition and create version payload without it
        version_payload = {k: v for k, v in evaluator_def.items() if k != 'name'}
        
        # Check if evaluator exists and compare definitions
        if not force:
            try:
                # Get latest version
                pager = client.evaluators.list_versions(name=evaluator_name)
                latest_version = None
                async for ver in pager:
                    latest_version = ver
                    break  # Get first (latest) version
                
                if latest_version:
                    # Convert to dict if needed
                    existing_def = dict(latest_version) if not isinstance(latest_version, dict) else latest_version
                    
                    # Normalize both definitions for comparison
                    normalized_existing = normalize_for_comparison(existing_def)
                    normalized_new = normalize_for_comparison(version_payload)
                    
                    if normalized_existing == normalized_new:
                        existing_version = existing_def.get('version', 'unknown') if isinstance(existing_def, dict) else getattr(existing_def, 'version', 'unknown')
                        print(f"  ⊘ Skipped (no changes): {evaluator_name} (version: {existing_version})")
                        return True, 'skipped'
                        
            except Exception:
                # If evaluator doesn't exist or error checking, proceed with creation
                pass
        
        # Create new version of evaluator
        response = await client.evaluators.create_version(
            name=evaluator_name,
            evaluator_version=version_payload
        )
        
        version = response.get('version', 'unknown') if isinstance(response, dict) else getattr(response, 'version', 'unknown')
        print(f"  ✓ Deployed: {evaluator_name} (version: {version})")
        return True, 'deployed'
        
    except Exception as e:
        print(f"  ✗ Failed to deploy {evaluator_name}: {e}")
        return False, 'failed'


async def list_existing_evaluators(client: AIProjectClient) -> List[Dict[str, str]]:
    """List all existing evaluators in the project."""
    try:
        evaluators = []
        pager = client.evaluators.list_latest_versions()
        
        async for evaluator in pager:
            name = evaluator.get('name') if isinstance(evaluator, dict) else getattr(evaluator, 'name', None)
            version = evaluator.get('version') if isinstance(evaluator, dict) else getattr(evaluator, 'version', None)
            if name:
                evaluators.append({'name': name, 'version': version})
        
        return evaluators
    except Exception as e:
        print(f"⚠️  Could not list existing evaluators: {e}")
        return []


async def delete_evaluator(client: AIProjectClient, evaluator_name: str, version: str = None) -> bool:
    """Delete an evaluator version from Azure AI Foundry."""
    try:
        if not version:
            # Get latest version
            pager = client.evaluators.list_versions(name=evaluator_name)
            versions = []
            async for v in pager:
                ver = v.get('version') if isinstance(v, dict) else getattr(v, 'version', None)
                if ver:
                    versions.append(ver)
            
            if not versions:
                print(f"  ⚠️  No versions found for {evaluator_name}")
                return False
            
            # Delete all versions
            for ver in versions:
                await client.evaluators.delete_version(name=evaluator_name, version=ver)
                print(f"  ✓ Deleted: {evaluator_name} (version: {ver})")
        else:
            await client.evaluators.delete_version(name=evaluator_name, version=version)
            print(f"  ✓ Deleted: {evaluator_name} (version: {version})")
        
        return True
    except Exception as e:
        print(f"  ✗ Failed to delete {evaluator_name}: {e}")
        return False


async def main():
    parser = argparse.ArgumentParser(
        description="Deploy custom evaluators to Azure AI Foundry"
    )
    parser.add_argument(
        '--environment',
        choices=['dev', 'test', 'prod'],
        default='dev',
        help='Target environment (default: dev)'
    )
    parser.add_argument(
        '--evaluators-dir',
        type=str,
        default='evaluations/custom-evaluators',
        help='Directory containing evaluator JSON files (default: evaluations/custom-evaluators)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Validate evaluators without deploying'
    )
    parser.add_argument(
        '--list',
        action='store_true',
        help='List existing evaluators in the project'
    )
    parser.add_argument(
        '--delete',
        type=str,
        metavar='EVALUATOR_NAME',
        help='Delete a specific evaluator (all versions)'
    )
    parser.add_argument(
        '--version',
        type=str,
        help='Specific version to delete (use with --delete)'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Force overwrite existing evaluators'
    )
    
    args = parser.parse_args()
    
    print("="*80)
    print("🎯 Azure AI Foundry - Custom Evaluators Deployment")
    print("="*80)
    
    try:
        # Load configuration
        print(f"\n📋 Loading configuration for environment: {args.environment}")
        config = load_environment_config(args.environment)
        print(f"  Endpoint: {config['endpoint']}")
        
        # Initialize Azure client
        print("\n🔐 Authenticating with Azure...")
        
        async with DefaultAzureCredential() as credential:
            async with AIProjectClient(
                endpoint=config['endpoint'],
                credential=credential
            ) as client:
                print("  ✓ Connected to Azure AI Foundry")
            
                # Handle list command
                if args.list:
                    print("\n📝 Listing existing evaluators...")
                    evaluators = await list_existing_evaluators(client)
                    if evaluators:
                        print(f"\nFound {len(evaluators)} evaluator(s):")
                        for evaluator in evaluators:
                            name = evaluator.get('name', 'unknown')
                            version = evaluator.get('version', 'unknown')
                            print(f"  - {name} (version: {version})")
                    else:
                        print("  No evaluators found")
                    return 0
                
                # Handle delete command
                if args.delete:
                    version_str = f" version {args.version}" if args.version else " (all versions)"
                    print(f"\n🗑️  Deleting evaluator: {args.delete}{version_str}")
                    success = await delete_evaluator(client, args.delete, args.version)
                    return 0 if success else 1
                
                # Load evaluator definitions
                evaluators_dir = Path(__file__).parent.parent.parent / args.evaluators_dir
                evaluators = load_evaluator_definitions(evaluators_dir)
                
                if not evaluators:
                    print("\n⚠️  No evaluators to deploy")
                    return 0
                
                # Check existing evaluators
                print("\n🔍 Checking existing evaluators...")
                existing = await list_existing_evaluators(client)
                existing_names = {e.get('name') for e in existing}
                
                # Deploy evaluators
                print(f"\n🚀 Deploying {len(evaluators)} evaluator(s)...")
                if args.dry_run:
                    print("   [DRY RUN MODE - No actual deployment]")
                
                if args.force:
                    print(f"   NOTE: Force mode - will create new versions even if unchanged")
                else:
                    print(f"   NOTE: Will skip evaluators with no changes")
                
                success_count = 0
                skip_count = 0
                fail_count = 0
                
                for evaluator_info in evaluators:
                    evaluator_def = evaluator_info['definition']
                    evaluator_name = evaluator_def.get('name', 'unknown')
                    
                    # Deploy (checks for changes unless force=True)
                    success, status = await deploy_evaluator(client, evaluator_def, args.dry_run, args.force)
                    
                    if status == 'deployed':
                        success_count += 1
                    elif status == 'skipped':
                        skip_count += 1
                    else:
                        fail_count += 1
                
                # Summary
                print("\n" + "="*80)
                print("📊 Deployment Summary")
                print("="*80)
                print(f"  ✓ Deployed: {success_count}")
                if skip_count > 0:
                    print(f"  ⊘ Skipped:  {skip_count}")
                if fail_count > 0:
                    print(f"  ✗ Failed:   {fail_count}")
                print("="*80)
                
                if fail_count > 0:
                    return 1
                
                print("\n✅ Deployment completed successfully!")
                return 0
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
