"""
Script to read conversation data from Azure AI Foundry.

This demonstrates how to access conversation data using the OpenAI-compatible API
through the AIProjectClient.
"""

import asyncio
import json
import sys
from azure.identity.aio import DefaultAzureCredential
from azure.ai.projects.aio import AIProjectClient


async def read_conversation(conversation_id: str):
    """
    Read conversation data from Azure AI Foundry.
    
    Args:
        conversation_id: The conversation ID (format: conv_xxxx...)
    """
    credential = DefaultAzureCredential()
    
    # Initialize the project client
    project_client = AIProjectClient(
        endpoint="https://sweeden-test.services.ai.azure.com",
        credential=credential,
        subscription_id="fce27326-d5b1-4a1e-b77c-5a869a831de1",
        resource_group_name="Dev",
        project_name="swe-proj"
    )
    
    print(f"Reading conversation: {conversation_id}\n")
    print("=" * 80)
    
    try:
        # Get the OpenAI-compatible client
        openai_client = project_client.get_openai_client()
        
        # Try to retrieve conversation data
        print("\n📝 Fetching conversation items...\n")
        
        try:
            # List items in the conversation
            items_response = await openai_client.conversations.items.list(
                conversation_id=conversation_id,
                limit=100,
                order="asc"  # Get items in chronological order
            )
            
            if hasattr(items_response, 'data'):
                items = items_response.data
                print(f"✓ Found {len(items)} items in conversation\n")
                
                for i, item in enumerate(items, 1):
                    print(f"\n{'='*80}")
                    print(f"Item {i}")
                    print(f"{'='*80}")
                    
                    item_dict = item.model_dump() if hasattr(item, 'model_dump') else dict(item)
                    print(json.dumps(item_dict, indent=2, default=str))
                    
                    # Extract readable content
                    if 'content' in item_dict:
                        print("\n📄 Content:")
                        for content_part in item_dict.get('content', []):
                            if isinstance(content_part, dict):
                                if content_part.get('type') == 'text':
                                    print(f"  {content_part.get('text', '')}")
            else:
                print("Full response:")
                print(json.dumps(items_response, indent=2, default=str))
                
        except Exception as e:
            error_msg = str(e)
            print(f"❌ Error reading conversation: {error_msg}")
            print(f"   Error type: {type(e).__name__}")
            
            if "404" in error_msg or "not found" in error_msg.lower():
                print("\nPossible reasons:")
                print("  - Conversation ID doesn't exist")
                print("  - Conversation is in a different project")
                print("  - Conversation has been deleted")
                print("  - Using wrong endpoint or project configuration")
            
            return False
        
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await project_client.close()
        await credential.close()
    
    return True


async def main():
    """Main entry point."""
    if len(sys.argv) > 1:
        conversation_id = sys.argv[1]
    else:
        # Default conversation ID
        conversation_id = "conv_7dcaba55a378ba9c002YRD9Ic1f36bUEHNlbJE2amVwIFCCaKW"
        print(f"No conversation ID provided, using default: {conversation_id}\n")
    
    success = await read_conversation(conversation_id)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
