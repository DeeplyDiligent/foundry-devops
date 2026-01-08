#!/usr/bin/env python3
"""
Script to retrieve traces using the Azure AI Foundry REST API.
Uses direct REST API calls with Azure Identity for authentication.

⚠️  IMPORTANT LIMITATION:
The ai.azure.com/nextgen/api endpoint requires browser-based session cookies
and does NOT accept standard Azure bearer tokens. This endpoint is designed
for the Azure AI Foundry portal web interface, not for programmatic access.

For programmatic access to traces, use get_traces.py which uses the Azure SDK
and the proper API endpoints (services.ai.azure.com).

This script is provided as a reference for the REST API structure, but will
likely fail with 401 Unauthorized unless you:
1. Extract and provide browser session cookies from an active portal session
2. Use a different API endpoint that supports bearer token authentication
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, Optional
import requests
from azure.identity import DefaultAzureCredential, InteractiveBrowserCredential
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
    
    # Get subscription ID from environment variable if it's a template
    subscription_id = env_config.get('subscription_id', '')
    if subscription_id.startswith('${') and subscription_id.endswith('}'):
        env_var = subscription_id[2:-1]
        subscription_id = os.environ.get(env_var, '')
    
    return {
        'endpoint': endpoint,
        'subscription_id': subscription_id,
        'resource_group': env_config.get('resource_group'),
        'account_name': account_name,
        'project_name': project_name
    }


def get_bearer_token(interactive: bool = False) -> str:
    """
    Get an Azure bearer token for ai.azure.com using Azure credentials.
    
    Args:
        interactive: Use interactive browser authentication
        
    Returns:
        Bearer token string
    """
    if interactive:
        credential = InteractiveBrowserCredential()
        print("Opening browser for authentication...")
    else:
        credential = DefaultAzureCredential()
    
    # Try different scopes for Azure AI Foundry
    scopes_to_try = [
        "https://management.azure.com/.default",
        "https://ml.azure.com/.default",
        "https://cognitiveservices.azure.com/.default",
    ]
    
    for scope in scopes_to_try:
        try:
            token = credential.get_token(scope)
            return token.token
        except Exception as e:
            continue
    
    # If none work, use management scope
    token = credential.get_token("https://management.azure.com/.default")
    return token.token


def build_resource_id(config: Dict[str, str]) -> str:
    """
    Build the Azure resource ID for the AI project.
    
    Format: /subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.CognitiveServices/accounts/{account}/projects/{project}
    """
    subscription_id = config['subscription_id']
    resource_group = config['resource_group']
    account_name = config['account_name']
    project_name = config['project_name']
    
    if not subscription_id:
        raise ValueError(
            "AZURE_SUBSCRIPTION_ID environment variable is not set. "
            "Please set it with: export AZURE_SUBSCRIPTION_ID=<your-subscription-id>"
        )
    
    return (f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}/"
            f"providers/Microsoft.CognitiveServices/accounts/{account_name}/"
            f"projects/{project_name}")


def get_traces_via_rest_api(
    agent_name: str,
    resource_id: str,
    bearer_token: Optional[str] = None,
    cookies: Optional[Dict[str, str]] = None,
    limit: int = 100,
    order: str = "desc"
) -> Dict[str, Any]:
    """
    Fetch traces using the Azure AI Foundry REST API.
    
    Args:
        agent_name: Name of the agent/workflow to get traces for
        resource_id: Azure resource ID for the AI project
        bearer_token: Bearer token for authentication (optional if using cookies)
        cookies: Session cookies from browser (optional if using bearer token)
        limit: Maximum number of traces to return
        order: Order of results (desc or asc)
        
    Returns:
        API response as dictionary
    """
    url = "https://ai.azure.com/nextgen/api/query?getResponsesResolver"
    
    headers = {
        "accept": "*/*",
        "content-type": "application/json",
        "x-ms-client-user-type": "Azure AI Foundry",
        "x-ms-user-agent": "AzureMachineLearningWorkspacePortal/AIFoundry",
        "x-ms-useragent": "AzureMachineLearningWorkspacePortal/AIFoundry"
    }
    
    if bearer_token:
        headers["authorization"] = f"Bearer {bearer_token}"
    
    body = {
        "query": "getResponsesResolver",
        "params": {
            "resourceId": resource_id,
            "agentName": agent_name,
            "limit": limit,
            "order": order
        },
        "paginationParams": {}
    }
    
    # Use cookies if provided, otherwise just headers
    if cookies:
        response = requests.post(url, headers=headers, json=body, cookies=cookies)
    else:
        response = requests.post(url, headers=headers, json=body)
    
    # Debug output
    if response.status_code != 200:
        print(f"\n⚠ Response Status: {response.status_code}")
        print(f"⚠ Response Headers: {dict(response.headers)}")
        print(f"⚠ Response Body: {response.text[:500]}")
    
    response.raise_for_status()
    
    return response.json()


def format_traces_table(traces_data: Dict[str, Any]) -> str:
    """Format traces data as a table."""
    if not traces_data or 'data' not in traces_data:
        return "No traces found"
    
    traces = traces_data.get('data', [])
    if not traces:
        return "No traces found"
    
    # Print header
    output = []
    output.append("\n" + "="*120)
    output.append(f"{'Trace ID':<45} {'Created At':<25} {'Status':<15} {'Agent':<30}")
    output.append("="*120)
    
    for trace in traces:
        trace_id = trace.get('id', 'N/A')[:45]
        
        # Handle Unix timestamp
        created_at = trace.get('created_at', 'N/A')
        if created_at != 'N/A' and isinstance(created_at, (int, float)):
            try:
                dt = datetime.fromtimestamp(created_at, tz=timezone.utc)
                created_at = dt.strftime('%Y-%m-%d %H:%M:%S UTC')
            except:
                pass
        
        status = trace.get('status', 'N/A')
        
        # Get agent name
        agent_info = trace.get('agent', {})
        if isinstance(agent_info, dict):
            agent_name = agent_info.get('name', 'N/A')
            agent_version = agent_info.get('version', '')
            agent = f"{agent_name} v{agent_version}" if agent_version else agent_name
        else:
            agent = 'N/A'
        agent = agent[:30]
        
        output.append(f"{trace_id:<45} {created_at:<25} {status:<15} {agent:<30}")
    
    output.append("="*120)
    output.append(f"\nTotal traces: {len(traces)}\n")
    
    return "\n".join(output)


def format_traces_json(traces_data: Dict[str, Any]) -> str:
    """Format traces data as JSON."""
    return json.dumps(traces_data, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Retrieve traces using Azure AI Foundry REST API"
    )
    parser.add_argument(
        '--environment',
        type=str,
        default='dev',
        help='Environment to use (default: dev)'
    )
    parser.add_argument(
        '--agent',
        type=str,
        required=True,
        help='Name of the agent or workflow to get traces for'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=100,
        help='Maximum number of traces to return (default: 100)'
    )
    parser.add_argument(
        '--order',
        type=str,
        default='desc',
        choices=['desc', 'asc'],
        help='Order of results (default: desc)'
    )
    parser.add_argument(
        '--output',
        type=str,
        default='table',
        choices=['table', 'json'],
        help='Output format (default: table)'
    )
    parser.add_argument(
        '--interactive',
        action='store_true',
        help='Use interactive browser authentication'
    )
    parser.add_argument(
        '--cookies',
        type=str,
        help='Browser session cookies (format: "name1=value1; name2=value2")'
    )
    
    args = parser.parse_args()
    
    if not args.cookies:
        print("="*80)
        print("IMPORTANT NOTE:")
        print("The ai.azure.com API uses browser-based session authentication.")
        print("Standard Azure bearer tokens may not work with this endpoint.")
        print()
        print("To use this script, extract cookies from your browser:")
        print("1. Open browser DevTools (F12) while logged into ai.azure.com")
        print("2. Go to Application/Storage > Cookies > https://ai.azure.com")
        print("3. Copy the cookie string and pass it with --cookies flag")
        print()
        print("Or use the SDK-based script: python scripts/runtime/get_traces.py")
        print("="*80)
        print()
    
    try:
        # Load configuration
        print(f"Loading configuration for environment: {args.environment}")
        config = load_environment_config(args.environment)
        
        # Build resource ID
        print("Building resource ID...")
        resource_id = build_resource_id(config)
        print(f"Resource ID: {resource_id}")
        
        # Parse cookies if provided
        cookies_dict = None
        if args.cookies:
            print("Using provided browser cookies...")
            cookies_dict = {}
            for cookie in args.cookies.split(';'):
                cookie = cookie.strip()
                if '=' in cookie:
                    name, value = cookie.split('=', 1)
                    cookies_dict[name.strip()] = value.strip()
            print(f"✓ Parsed {len(cookies_dict)} cookies")
        else:
            # Get bearer token
            print("Authenticating with Azure...")
            bearer_token = get_bearer_token(args.interactive)
            print("✓ Authentication successful")
        
        # Fetch traces
        print(f"\nFetching traces for agent: {args.agent}")
        print(f"  Limit: {args.limit}")
        print(f"  Order: {args.order}")
        
        if cookies_dict:
            traces_data = get_traces_via_rest_api(
                agent_name=args.agent,
                resource_id=resource_id,
                cookies=cookies_dict,
                limit=args.limit,
                order=args.order
            )
        else:
            traces_data = get_traces_via_rest_api(
                agent_name=args.agent,
                resource_id=resource_id,
                bearer_token=bearer_token,
                limit=args.limit,
                order=args.order
            )
        
        # Format and display output
        if args.output == 'table':
            print(format_traces_table(traces_data))
        else:
            print(format_traces_json(traces_data))
        
        print("✓ Done")
        
    except Exception as e:
        print(f"\n❌ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
