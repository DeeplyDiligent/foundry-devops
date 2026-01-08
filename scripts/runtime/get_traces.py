#!/usr/bin/env python3
"""
Script to retrieve traces for conversations in the last 5 minutes.
Uses Azure AI SDK to fetch thread message history from Microsoft Foundry.
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from azure.identity.aio import DefaultAzureCredential
from azure.ai.projects.aio import AIProjectClient
from azure.ai.agents.aio import AgentsClient
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


class TraceRetriever:
    """Retrieves conversation traces from Microsoft Foundry."""
    
    def __init__(self, environment: str):
        self.environment = environment
        self.config = load_environment_config(environment)
        self.credential = None
        self.project_client = None
        self.agents_client = None
        
    async def __aenter__(self):
        """Async context manager entry."""
        self.credential = DefaultAzureCredential()
        self.project_client = AIProjectClient(
            endpoint=self.config['endpoint'],
            credential=self.credential
        )
        # Also create agents client for accessing threads/messages
        self.agents_client = AgentsClient(
            endpoint=self.config['endpoint'],
            credential=self.credential
        )
        print(f"✓ Connected to Microsoft Foundry ({self.environment})")
        print(f"  Endpoint: {self.config['endpoint']}")
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self.agents_client:
            await self.agents_client.close()
        if self.project_client:
            await self.project_client.close()
        if self.credential:
            await self.credential.close()
    
    async def get_recent_traces(self, minutes: int = 5) -> List[Dict[str, Any]]:
        """
        Retrieve conversation traces (thread messages) from the last N minutes.
        
        Args:
            minutes: Number of minutes to look back (default: 5)
            
        Returns:
            List of conversation traces with messages
        """
        print(f"\n📊 Fetching conversation traces from the last {minutes} minutes...")
        
        # Calculate time range
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=minutes)
        
        print(f"  Time range: {start_time.isoformat()} to {end_time.isoformat()}")
        
        traces = []
        
        try:
            # List all agents to find their threads
            print("\n  Listing agents...")
            agents_list = self.project_client.agents.list()
            agent_count = 0
            
            async for agent in agents_list:
                agent_count += 1
                # Get version from versions.latest.version
                version = 'unknown'
                if hasattr(agent, 'versions') and agent.versions:
                    latest = agent.versions.get('latest', {})
                    version = latest.get('version', 'unknown')
                print(f"    Agent: {agent.name} (v{version})")
            
            if agent_count == 0:
                print("  ⚠ No agents found")
                return traces
            
            print(f"\n  Found {agent_count} agent(s)")
            
            # List threads from the agents client
            # Note: This retrieves all threads across all pages - we'll filter by time
            print("\n  Listing threads...")
            threads_pager = self.agents_client.threads.list(limit=100)
            
            thread_count = 0
            page_count = 0
            
            # Iterate through all pages of threads
            async for thread in threads_pager:
                if thread_count % 100 == 0 and thread_count > 0:
                    page_count += 1
                    print(f"    Fetching page {page_count + 1}...")
                
                thread_count += 1
                thread_id = thread.id
                thread_created_at = getattr(thread, 'created_at', None)
                
                # Check if thread is within time range
                if thread_created_at:
                    # created_at might be datetime or Unix timestamp
                    if isinstance(thread_created_at, datetime):
                        thread_time = thread_created_at
                        if thread_time.tzinfo is None:
                            thread_time = thread_time.replace(tzinfo=timezone.utc)
                    else:
                        thread_time = datetime.fromtimestamp(thread_created_at, tz=timezone.utc)
                    
                    if thread_time < start_time:
                        continue  # Skip old threads
                
                print(f"    Thread: {thread_id}")
                
                # Get the agent ID from runs
                agent_info = await self.get_thread_agent(thread_id)
                
                # Get messages for this thread
                messages_data = await self.get_thread_messages(thread_id, start_time)
                
                if messages_data['messages']:
                    traces.append({
                        'thread_id': thread_id,
                        'thread_created_at': thread_created_at,
                        'agent_id': agent_info.get('agent_id'),
                        'agent_name': agent_info.get('agent_name'),
                        'message_count': messages_data['count'],
                        'messages': messages_data['messages']
                    })
            
            print(f"\n  Processed {thread_count} thread(s) across {page_count + 1} page(s)")
            print(f"  Found {len(traces)} thread(s) with messages in time range")
            
        except Exception as e:
            print(f"  ⚠ Error retrieving traces: {e}")
            import traceback
            traceback.print_exc()
        
        return traces
    
    async def get_thread_agent(self, thread_id: str) -> Dict[str, Any]:
        """
        Get the agent ID and name associated with a thread by checking its runs.
        
        Args:
            thread_id: The thread ID
            
        Returns:
            Dict with agent_id and agent_name
        """
        agent_info = {'agent_id': None, 'agent_name': None}
        
        try:
            # List runs for this thread to find the agent
            runs_pager = self.agents_client.runs.list(thread_id=thread_id, limit=1)
            
            async for run in runs_pager:
                agent_id = getattr(run, 'agent_id', None) or getattr(run, 'assistant_id', None)
                if agent_id:
                    agent_info['agent_id'] = agent_id
                    # Try to get agent name using the AgentsClient
                    try:
                        # Need to use the sync client for get_agent
                        # Create a temporary sync client
                        from azure.ai.agents import AgentsClient as SyncAgentsClient
                        from azure.identity import DefaultAzureCredential as SyncCredential
                        
                        sync_cred = SyncCredential()
                        sync_client = SyncAgentsClient(self.config['endpoint'], sync_cred)
                        agent = sync_client.get_agent(agent_id)
                        if agent and hasattr(agent, 'name'):
                            agent_info['agent_name'] = agent.name
                    except:
                        pass
                break
        except Exception as e:
            pass
        
        return agent_info
    
    async def get_specific_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific message/response by ID by searching through threads and runs.
        
        Args:
            message_id: The message/response ID to find (can be msg_* or resp_*)
            
        Returns:
            Message data if found, None otherwise
        """
        print(f"\n🔍 Searching for message/response: {message_id}")
        
        try:
            # List threads
            threads_pager = self.agents_client.threads.list(limit=100)
            
            async for thread in threads_pager:
                thread_id = thread.id
                
                # Check runs in this thread (for resp_* IDs)
                try:
                    runs_pager = self.agents_client.runs.list(thread_id=thread_id, limit=100)
                    
                    async for run in runs_pager:
                        run_id = run.id
                        response_id = getattr(run, 'response_id', None)
                        print(f"  Checking run: {run_id}", end='')
                        if response_id:
                            print(f" (response_id: {response_id})")
                        else:
                            print()
                        
                        # Check if run ID matches
                        if run_id == message_id or response_id == message_id:
                            print(f"  ✓ Found response in thread: {thread_id}")
                            
                            run_data = {
                                'id': run_id,
                                'thread_id': thread_id,
                                'status': getattr(run, 'status', None),
                                'created_at': getattr(run, 'created_at', None),
                                'agent_id': getattr(run, 'agent_id', None) or getattr(run, 'assistant_id', None),
                                'raw': str(run)
                            }
                            
                            return run_data
                except Exception as e:
                    pass
                
                # Search messages in this thread (for msg_* IDs)
                try:
                    messages_pager = self.agents_client.messages.list(
                        thread_id=thread_id,
                        limit=100
                    )
                    
                    async for message in messages_pager:
                        msg_id = message.id
                        print(f"  Checking message: {msg_id}")
                        
                        if msg_id == message_id:
                            print(f"  ✓ Found message in thread: {thread_id}")
                            
                            # Extract message details
                            msg_data = {
                                'id': message.id,
                                'thread_id': thread_id,
                                'role': getattr(message, 'role', None),
                                'created_at': getattr(message, 'created_at', None),
                                'content': []
                            }
                            
                            # Extract content
                            if hasattr(message, 'content') and message.content:
                                for content_item in message.content:
                                    if hasattr(content_item, 'text'):
                                        msg_data['content'].append({
                                            'type': 'text',
                                            'text': getattr(content_item.text, 'value', str(content_item.text))
                                        })
                                    elif hasattr(content_item, 'type'):
                                        msg_data['content'].append({
                                            'type': getattr(content_item, 'type', 'unknown'),
                                            'data': str(content_item)
                                        })
                            
                            # Get full message object as dict
                            msg_data['raw'] = str(message)
                            
                            return msg_data
                except Exception as e:
                    continue
                    
        except Exception as e:
            print(f"  ⚠ Error searching: {e}")
        
        print(f"  ✗ Message/response not found in threads")
        print(f"  ℹ This ID might only be accessible via the browser portal API")
        return None
    
    async def get_thread_messages(self, thread_id: str, start_time: datetime) -> Dict[str, Any]:
        """
        Get all messages from a thread, filtered by time.
        
        Args:
            thread_id: The thread ID
            start_time: Only include messages after this time
            
        Returns:
            Dict with message count and list of messages
        """
        messages_list = []
        count = 0
        
        try:
            # List messages with pagination
            messages_pager = self.agents_client.messages.list(
                thread_id=thread_id,
                limit=50  # Items per page
            )
            
            async for message in messages_pager:
                count += 1
                created_at = getattr(message, 'created_at', None)
                
                # Filter by time
                if created_at:
                    # Handle both datetime and Unix timestamp
                    if isinstance(created_at, datetime):
                        msg_time = created_at
                        if msg_time.tzinfo is None:
                            msg_time = msg_time.replace(tzinfo=timezone.utc)
                    else:
                        msg_time = datetime.fromtimestamp(created_at, tz=timezone.utc)
                    
                    if msg_time < start_time:
                        continue
                
                # Extract message details
                msg_data = {
                    'id': message.id,
                    'role': getattr(message, 'role', None),
                    'created_at': created_at,
                    'content': []
                }
                
                # Extract content
                if hasattr(message, 'content') and message.content:
                    for content_item in message.content:
                        if hasattr(content_item, 'text'):
                            msg_data['content'].append({
                                'type': 'text',
                                'text': getattr(content_item.text, 'value', str(content_item.text))
                            })
                        elif hasattr(content_item, 'type'):
                            msg_data['content'].append({
                                'type': getattr(content_item, 'type', 'unknown'),
                                'data': str(content_item)
                            })
                
                messages_list.append(msg_data)
            
        except Exception as e:
            print(f"      ⚠ Error reading messages: {e}")
        
        return {
            'count': len(messages_list),
            'messages': messages_list
        }
    
    
    def display_traces(self, traces: List[Dict[str, Any]], output_format: str = "json"):
        """
        Display or save traces in the specified format.
        
        Args:
            traces: List of trace objects
            output_format: Output format (json, table, or file path)
        """
        if not traces:
            print("\n  No conversation traces found")
            return
        
        print(f"\n📋 Displaying {len(traces)} conversation trace(s):")
        
        if output_format == "json":
            print(json.dumps(traces, indent=2, default=str))
        elif output_format == "table":
            # Simple table display
            for i, trace in enumerate(traces, 1):
                print(f"\n  Thread {i}: {trace.get('thread_id')}")
                print(f"    Agent: {trace.get('agent_name', 'Unknown')} ({trace.get('agent_id', 'N/A')})")
                print(f"    Messages: {trace.get('message_count', 0)}")
                
                # Handle thread_created_at (might be datetime or timestamp)
                created_at = trace.get('thread_created_at')
                if created_at:
                    if isinstance(created_at, datetime):
                        created_str = created_at.isoformat()
                    else:
                        created_str = datetime.fromtimestamp(created_at, tz=timezone.utc).isoformat()
                else:
                    created_str = 'Unknown'
                print(f"    Created: {created_str}")
                
                for j, msg in enumerate(trace.get('messages', []), 1):
                    # Handle message created_at
                    msg_created = msg.get('created_at')
                    if msg_created:
                        if isinstance(msg_created, datetime):
                            msg_time = msg_created.isoformat()
                        else:
                            msg_time = datetime.fromtimestamp(msg_created, tz=timezone.utc).isoformat()
                    else:
                        msg_time = 'Unknown'
                    
                    print(f"      Message {j} ({msg.get('role', 'unknown')}): {msg_time}")
                    for content in msg.get('content', []):
                        if content.get('type') == 'text':
                            text = content.get('text', '')
                            # Truncate long messages
                            if len(text) > 100:
                                text = text[:100] + "..."
                            print(f"        {text}")
        else:
            # Save to file
            output_path = Path(output_format)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, 'w') as f:
                json.dump(traces, f, indent=2, default=str)
            
            print(f"  ✓ Traces saved to: {output_path}")


async def async_main():
    parser = argparse.ArgumentParser(
        description="Retrieve conversation traces from Microsoft Foundry"
    )
    parser.add_argument(
        "--environment",
        choices=["dev", "test", "prod"],
        default="dev",
        help="Target environment (default: dev)"
    )
    parser.add_argument(
        "--minutes",
        type=int,
        default=5,
        help="Number of minutes to look back (default: 5)"
    )
    parser.add_argument(
        "--output",
        default="json",
        help="Output format: 'json', 'table', or file path (default: json)"
    )
    parser.add_argument(
        "--message-id",
        type=str,
        help="Retrieve a specific message/response by ID"
    )
    
    args = parser.parse_args()
    
    try:
        async with TraceRetriever(args.environment) as retriever:
            # Check if searching for specific message
            if args.message_id:
                message = await retriever.get_specific_message(args.message_id)
                if message:
                    if args.output == "json":
                        print(json.dumps(message, indent=2, default=str))
                    else:
                        print("\n" + "="*80)
                        print(f"MESSAGE: {message['id']}")
                        print("="*80)
                        print(f"Thread ID: {message['thread_id']}")
                        print(f"Role: {message['role']}")
                        print(f"Created: {message['created_at']}")
                        print(f"\nContent:")
                        for content in message['content']:
                            if content['type'] == 'text':
                                print(f"  {content['text']}")
                            else:
                                print(f"  [{content['type']}]: {content.get('data', 'N/A')}")
                        print("="*80)
                    return 0
                else:
                    print(f"\n✗ Message {args.message_id} not found")
                    return 1
            
            # Retrieve traces (thread messages)
            traces = await retriever.get_recent_traces(args.minutes)
            
            # Display results
            retriever.display_traces(traces, args.output)
            
            if traces:
                print(f"\n✓ Successfully retrieved {len(traces)} conversation trace(s)")
                return 0
            else:
                print("\n⚠ No conversation traces found in the specified time range")
                return 0
    
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


def main():
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
