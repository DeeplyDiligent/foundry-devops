#!/usr/bin/env python3
"""
Script to retrieve traces using the Azure OpenAI/Foundry REST API.
Uses the official v1 responses API endpoint with proper token authentication.

This approach uses the documented Azure AI Foundry API endpoints
that accept standard Azure bearer tokens.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List
import requests
from azure.identity import DefaultAzureCredential
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
    
    # Parse endpoint to extract account_name and project_name
    # Format: https://{account}.services.ai.azure.com/api/projects/{project}
    endpoint = env_config['azure_project_endpoint']
    
    # Extract account name from URL
    import re
    account_match = re.search(r'https://([^.]+)\.services\.ai\.azure\.com', endpoint)
    account_name = account_match.group(1) if account_match else None
    
    # Extract project name from URL
    project_match = re.search(r'/api/projects/([^/]+)', endpoint)
    project_name = project_match.group(1) if project_match else None
    
    # Build base endpoint for OpenAI API (without /api/projects path)
    if account_name:
        base_endpoint = f"https://{account_name}.services.ai.azure.com"
    else:
        base_endpoint = endpoint.split('/api/')[0] if '/api/' in endpoint else endpoint
    
    return {
        'endpoint': endpoint,
        'base_endpoint': base_endpoint,
        'account_name': account_name,
        'project_name': project_name
    }


def get_bearer_token() -> str:
    """
    Get an Azure bearer token for Azure AI Foundry API.
    
    Returns:
        Bearer token string
    """
    credential = DefaultAzureCredential()
    
    # Try different scopes - the error message indicates https://ai.azure.com might be needed
    scopes_to_try = [
        "https://ai.azure.com/.default",
        "https://cognitiveservices.azure.com/.default",
        "https://ml.azure.com/.default"
    ]
    
    for scope in scopes_to_try:
        try:
            print(f"  Trying scope: {scope}")
            token = credential.get_token(scope)
            print(f"  ✓ Got token with scope: {scope}")
            return token.token
        except Exception as e:
            print(f"  ✗ Failed with scope {scope}: {e}")
            continue
    
    # Fall back to cognitive services
    token = credential.get_token("https://cognitiveservices.azure.com/.default")
    return token.token


def get_response_by_id(
    endpoint: str,
    response_id: str,
    bearer_token: str,
    api_version: str = "preview",
    use_project_scope: bool = True
) -> Dict[str, Any]:
    """
    Retrieve a specific response by ID using the OpenAI v1 API.
    
    Args:
        endpoint: Full endpoint including /api/projects/{project} or base endpoint
        response_id: The response ID to retrieve
        bearer_token: Bearer token for authentication
        api_version: API version (default: preview)
        use_project_scope: If True, use the full project endpoint path
        
    Returns:
        Response data as dictionary
    """
    # If using project scope and endpoint has /api/projects, use it as-is
    # Otherwise construct the path
    if use_project_scope and '/api/projects/' in endpoint:
        url = f"{endpoint}/openai/v1/responses/{response_id}"
    else:
        url = f"{endpoint}/openai/v1/responses/{response_id}"
    
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json"
    }
    
    params = {
        "api-version": api_version
    }
    
    response = requests.get(url, headers=headers, params=params)
    
    # Debug output for errors
    if response.status_code != 200:
        print(f"\n⚠ Response Status: {response.status_code}")
        print(f"⚠ Response Headers: {dict(response.headers)}")
        print(f"⚠ Response Body: {response.text[:500]}")
    
    response.raise_for_status()
    
    return response.json()


def list_responses(
    endpoint: str,
    bearer_token: str,
    limit: int = 100,
    api_version: str = "preview",
    use_project_scope: bool = True
) -> Dict[str, Any]:
    """
    List responses using the OpenAI v1 API.
    
    Args:
        endpoint: Full endpoint including /api/projects/{project} or base endpoint
        bearer_token: Bearer token for authentication
        limit: Maximum number of responses to return
        api_version: API version (default: preview)
        use_project_scope: If True, use the full project endpoint path
        
    Returns:
        List of responses as dictionary
    """
    if use_project_scope and '/api/projects/' in endpoint:
        url = f"{endpoint}/openai/v1/responses"
    else:
        url = f"{endpoint}/openai/v1/responses"
    
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json"
    }
    
    params = {
        "api-version": api_version,
        "limit": limit
    }
    
    response = requests.get(url, headers=headers, params=params)
    
    # Debug output for errors
    if response.status_code != 200:
        print(f"\n⚠ Response Status: {response.status_code}")
        print(f"⚠ Response Headers: {dict(response.headers)}")
        print(f"⚠ Response Body: {response.text[:500]}")
    
    response.raise_for_status()
    
    return response.json()


def format_response_table(response_data: Dict[str, Any]) -> str:
    """Format a single response as a table."""
    output = []
    output.append("\n" + "="*120)
    output.append("RESPONSE DETAILS")
    output.append("="*120)
    
    # Basic info
    output.append(f"ID:         {response_data.get('id', 'N/A')}")
    output.append(f"Status:     {response_data.get('status', 'N/A')}")
    output.append(f"Object:     {response_data.get('object', 'N/A')}")
    
    # Created at
    created_at = response_data.get('created_at', 'N/A')
    if created_at != 'N/A' and isinstance(created_at, (int, float)):
        try:
            dt = datetime.fromtimestamp(created_at, tz=timezone.utc)
            created_at = dt.strftime('%Y-%m-%d %H:%M:%S UTC')
        except:
            pass
    output.append(f"Created:    {created_at}")
    
    # Agent info
    agent_info = response_data.get('agent', {})
    if isinstance(agent_info, dict):
        agent_name = agent_info.get('name', 'N/A')
        agent_version = agent_info.get('version', 'N/A')
        output.append(f"Agent:      {agent_name} v{agent_version}")
    
    # Usage
    usage = response_data.get('usage', {})
    if usage:
        output.append(f"\nUsage:")
        output.append(f"  Input tokens:  {usage.get('input_tokens', 0)}")
        output.append(f"  Output tokens: {usage.get('output_tokens', 0)}")
        output.append(f"  Total tokens:  {usage.get('total_tokens', 0)}")
    
    # Output
    output_items = response_data.get('output', [])
    if output_items:
        output.append(f"\nOutput ({len(output_items)} items):")
        for i, item in enumerate(output_items[:5], 1):  # Show first 5
            item_type = item.get('type', 'unknown')
            output.append(f"  {i}. Type: {item_type}")
            
            if item_type == 'message':
                role = item.get('role', 'N/A')
                content = item.get('content', [])
                output.append(f"     Role: {role}")
                if content and isinstance(content, list):
                    for c in content[:1]:  # Show first content
                        if c.get('type') == 'output_text':
                            text = c.get('text', '')[:100]
                            output.append(f"     Text: {text}...")
    
    output.append("="*120)
    
    return "\n".join(output)


def format_responses_table(responses_data: Dict[str, Any]) -> str:
    """Format multiple responses as a table."""
    if not responses_data or 'data' not in responses_data:
        return "No responses found"
    
    responses = responses_data.get('data', [])
    if not responses:
        return "No responses found"
    
    # Print header
    output = []
    output.append("\n" + "="*120)
    output.append(f"{'Response ID':<45} {'Created At':<25} {'Status':<15} {'Agent':<30}")
    output.append("="*120)
    
    for response in responses:
        response_id = response.get('id', 'N/A')[:45]
        
        # Handle Unix timestamp
        created_at = response.get('created_at', 'N/A')
        if created_at != 'N/A' and isinstance(created_at, (int, float)):
            try:
                dt = datetime.fromtimestamp(created_at, tz=timezone.utc)
                created_at = dt.strftime('%Y-%m-%d %H:%M:%S UTC')
            except:
                pass
        
        status = response.get('status', 'N/A')
        
        # Get agent name
        agent_info = response.get('agent', {})
        if isinstance(agent_info, dict):
            agent_name = agent_info.get('name', 'N/A')
            agent_version = agent_info.get('version', '')
            agent = f"{agent_name} v{agent_version}" if agent_version else agent_name
        else:
            agent = 'N/A'
        agent = agent[:30]
        
        output.append(f"{response_id:<45} {created_at:<25} {status:<15} {agent:<30}")
    
    output.append("="*120)
    output.append(f"\nTotal responses: {len(responses)}\n")
    
    return "\n".join(output)


def format_json(data: Dict[str, Any]) -> str:
    """Format data as JSON."""
    return json.dumps(data, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Retrieve responses using Azure AI Foundry v1 REST API"
    )
    parser.add_argument(
        '--environment',
        type=str,
        default='dev',
        help='Environment to use (default: dev)'
    )
    parser.add_argument(
        '--response-id',
        type=str,
        help='Specific response ID to retrieve'
    )
    parser.add_argument(
        '--list',
        action='store_true',
        help='List all responses'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=100,
        help='Maximum number of responses to return when listing (default: 100)'
    )
    parser.add_argument(
        '--output',
        type=str,
        default='table',
        choices=['table', 'json'],
        help='Output format (default: table)'
    )
    parser.add_argument(
        '--api-version',
        type=str,
        default='preview',
        help='API version to use (default: preview)'
    )
    parser.add_argument(
        '--project-scope',
        action='store_true',
        default=True,
        help='Use project-scoped endpoint (default: True)'
    )
    
    args = parser.parse_args()
    
    if not args.response_id and not args.list:
        parser.error("Either --response-id or --list must be specified")
    
    try:
        # Load configuration
        print(f"Loading configuration for environment: {args.environment}")
        config = load_environment_config(args.environment)
        
        # Use full endpoint for project scope, base for account scope
        endpoint_to_use = config['endpoint'] if args.project_scope else config['base_endpoint']
        print(f"Endpoint: {endpoint_to_use}")
        
        # Get bearer token
        print("Authenticating with Azure...")
        bearer_token = get_bearer_token()
        print("✓ Authentication successful")
        
        # Retrieve response(s)
        if args.response_id:
            print(f"\nRetrieving response: {args.response_id}")
            response_data = get_response_by_id(
                endpoint=endpoint_to_use,
                response_id=args.response_id,
                bearer_token=bearer_token,
                api_version=args.api_version,
                use_project_scope=args.project_scope
            )
            
            # Format and display output
            if args.output == 'table':
                print(format_response_table(response_data))
            else:
                print(format_json(response_data))
        
        elif args.list:
            print(f"\nListing responses (limit: {args.limit})")
            responses_data = list_responses(
                endpoint=endpoint_to_use,
                bearer_token=bearer_token,
                limit=args.limit,
                api_version=args.api_version,
                use_project_scope=args.project_scope
            )
            
            # Format and display output
            if args.output == 'table':
                print(format_responses_table(responses_data))
            else:
                print(format_json(responses_data))
        
        print("✓ Done")
        
    except Exception as e:
        print(f"\n❌ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
