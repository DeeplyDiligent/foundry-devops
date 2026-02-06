"""
Fetch conversation with messages from Cosmos DB.
This script retrieves a conversation and all its related messages in a single nested structure.
Uses Azure Managed Identity (DefaultAzureCredential) for authentication.
"""

import os
from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from typing import Optional, Dict, Any, List

# Cosmos DB configuration from Azure CLI discovery
COSMOS_ENDPOINT = "https://gu5vcosmosdb.documents.azure.com:443/"
DATABASE_NAME = "enterprise_memory"
CONTAINER_NAME = "5a255147-e837-4ff0-b3ae-37f23a5191e6-run-state-v1"


def get_conversation_with_messages(
    conversation_id: str,
    partition_id: str,
    cosmos_endpoint: str = None,
    database_name: str = None,
    container_name: str = None
) -> Optional[Dict[str, Any]]:
    """
    Fetch a conversation with all its messages and edge data.
    Uses Azure Managed Identity (DefaultAzureCredential) for authentication.
    
    Args:
        conversation_id: The conversation ID (e.g., "conv_moZHeudIECYHGMJjzE0y8treaL8pDQc9")
        partition_id: The partition ID (e.g., "aisvcgu5v@projectgu5v@AML/182b7c44a09a431100")
        cosmos_endpoint: Cosmos DB endpoint URL (defaults to COSMOS_ENDPOINT constant)
        database_name: Name of the Cosmos database (defaults to DATABASE_NAME constant)
        container_name: Name of the container (defaults to CONTAINER_NAME constant)
    
    Returns:
        Dictionary with conversation data, conv2item edge, and nested messages
    """
    # Use constants if not provided
    cosmos_endpoint = cosmos_endpoint or COSMOS_ENDPOINT
    database_name = database_name or DATABASE_NAME
    container_name = container_name or CONTAINER_NAME
    
    # Initialize Cosmos client with Managed Identity
    credential = DefaultAzureCredential()
    client = CosmosClient(cosmos_endpoint, credential)
    database = client.get_database_client(database_name)
    container = database.get_container_client(container_name)
    
    # Query 1: Get the conversation document
    conversation_query = """
        SELECT *
        FROM c
        WHERE c.id = @conversation_id
          AND c.object.object_type = "conversation"
          AND c.partition_id = @partition_id
    """
    
    conversation_params = [
        {"name": "@conversation_id", "value": conversation_id},
        {"name": "@partition_id", "value": partition_id}
    ]
    
    conversations = list(container.query_items(
        query=conversation_query,
        parameters=conversation_params,
        enable_cross_partition_query=True
    ))
    
    if not conversations:
        print(f"Conversation {conversation_id} not found")
        return None
    
    conversation = conversations[0]
    
    # Query 2: Get the edge document (conversation2item)
    edge_query = """
        SELECT *
        FROM c
        WHERE c.object.object_type = "edge.conversation2item"
          AND STARTSWITH(c.id, @edge_prefix)
          AND c.partition_id = @partition_id
    """
    
    edge_prefix = f"conv2item_{conversation_id}"
    edge_params = [
        {"name": "@edge_prefix", "value": edge_prefix},
        {"name": "@partition_id", "value": partition_id}
    ]
    
    edges = list(container.query_items(
        query=edge_query,
        parameters=edge_params,
        enable_cross_partition_query=True
    ))
    
    edge_doc = edges[0] if edges else None
    
    # Query 3: Get all message documents for this conversation
    # Extract the partition_id from the conversation (just the suffix part)
    partition_suffix = partition_id.split('/')[-1] if '/' in partition_id else partition_id
    
    messages_query = """
        SELECT *
        FROM c
        WHERE c.object.object_type = "item"
          AND c.partition_id = @partition_id
          AND ARRAY_LENGTH(c.object.raw_rapi_serialized_item.conversation_ids) > 0
    """
    
    message_params = [
        {"name": "@partition_id", "value": partition_id}
    ]
    
    all_messages = list(container.query_items(
        query=messages_query,
        parameters=message_params,
        enable_cross_partition_query=True
    ))
    
    # Filter messages that belong to this conversation (client-side filtering)
    conversation_messages = []
    for msg in all_messages:
        conv_ids = msg.get("object", {}).get("raw_rapi_serialized_item", {}).get("conversation_ids", [])
        for conv_id_obj in conv_ids:
            if conv_id_obj.get("id") == conversation_id:
                conversation_messages.append(msg)
                break
    
    # Sort messages by creation time
    conversation_messages.sort(key=lambda m: m.get("info", {}).get("created_at", 0))
    
    # Build the final nested structure
    result = {
        "id": conversation["id"],
        "info": conversation.get("info", {}),
        "metadata": conversation.get("metadata", {}),
        "object": conversation.get("object", {}),
        "partition_id": conversation.get("partition_id", ""),
        "conv2item": None,
        "_rid": conversation.get("_rid", ""),
        "_self": conversation.get("_self", ""),
        "_etag": conversation.get("_etag", ""),
        "_attachments": conversation.get("_attachments", ""),
        "_ts": conversation.get("_ts", 0)
    }
    
    # Add conv2item edge with nested messages
    if edge_doc:
        result["conv2item"] = {
            "id": edge_doc["id"],
            "info": edge_doc.get("info", {}),
            "metadata": edge_doc.get("metadata", {}),
            "object": edge_doc.get("object", {}),
            "partition_id": edge_doc.get("partition_id", ""),
            "_rid": edge_doc.get("_rid", ""),
            "_self": edge_doc.get("_self", ""),
            "_etag": edge_doc.get("_etag", ""),
            "_attachments": edge_doc.get("_attachments", ""),
            "_ts": edge_doc.get("_ts", 0),
            "messages": conversation_messages
        }
    
    return result


def get_all_conversations_by_metadata(
    uid: str,
    verified: bool,
    cosmos_endpoint: str = None,
    database_name: str = None,
    container_name: str = None
) -> List[Dict[str, Any]]:
    """
    Fetch all conversations with specific metadata and their messages.
    Uses Azure Managed Identity (DefaultAzureCredential) for authentication.
    
    Args:
        uid: The user ID to filter by
        verified: The verified status to filter by
        cosmos_endpoint: Cosmos DB endpoint URL (defaults to COSMOS_ENDPOINT constant)
        database_name: Name of the Cosmos database (defaults to DATABASE_NAME constant)
        container_name: Name of the container (defaults to CONTAINER_NAME constant)
    
    Returns:
        List of dictionaries with conversation data, conv2item edge, and nested messages
    """
    # Use constants if not provided
    cosmos_endpoint = cosmos_endpoint or COSMOS_ENDPOINT
    database_name = database_name or DATABASE_NAME
    container_name = container_name or CONTAINER_NAME
    
    # Initialize Cosmos client with Managed Identity
    credential = DefaultAzureCredential()
    client = CosmosClient(cosmos_endpoint, credential)
    database = client.get_database_client(database_name)
    container = database.get_container_client(container_name)
    
    # Query: Get all conversations with specific metadata
    conversations_query = """
        SELECT *
        FROM c
        WHERE c.object.object_type = "conversation"
          AND c.metadata.uid = @uid
          AND c.metadata.verified = @verified
    """
    
    conversations_params = [
        {"name": "@uid", "value": uid},
        {"name": "@verified", "value": verified}
    ]
    
    conversations = list(container.query_items(
        query=conversations_query,
        parameters=conversations_params,
        enable_cross_partition_query=True
    ))
    
    print(f"Found {len(conversations)} conversations with uid={uid}, verified={verified}")
    
    # Process each conversation
    results = []
    for conversation in conversations:
        conversation_id = conversation["id"]
        partition_id = conversation["partition_id"]
        
        print(f"Processing conversation: {conversation_id}")
        
        # Get the edge document (conversation2item)
        edge_query = """
            SELECT *
            FROM c
            WHERE c.object.object_type = "edge.conversation2item"
              AND STARTSWITH(c.id, @edge_prefix)
              AND c.partition_id = @partition_id
        """
        
        edge_prefix = f"conv2item_{conversation_id}"
        edge_params = [
            {"name": "@edge_prefix", "value": edge_prefix},
            {"name": "@partition_id", "value": partition_id}
        ]
        
        edges = list(container.query_items(
            query=edge_query,
            parameters=edge_params,
            enable_cross_partition_query=True
        ))
        
        edge_doc = edges[0] if edges else None
        
        # Get all message documents for this conversation
        messages_query = """
            SELECT *
            FROM c
            WHERE c.object.object_type = "item"
              AND c.partition_id = @partition_id
              AND ARRAY_LENGTH(c.object.raw_rapi_serialized_item.conversation_ids) > 0
        """
        
        message_params = [
            {"name": "@partition_id", "value": partition_id}
        ]
        
        all_messages = list(container.query_items(
            query=messages_query,
            parameters=message_params,
            enable_cross_partition_query=True
        ))
        
        # Filter messages that belong to this conversation (client-side filtering)
        conversation_messages = []
        for msg in all_messages:
            conv_ids = msg.get("object", {}).get("raw_rapi_serialized_item", {}).get("conversation_ids", [])
            for conv_id_obj in conv_ids:
                if conv_id_obj.get("id") == conversation_id:
                    conversation_messages.append(msg)
                    break
        
        # Sort messages by creation time
        conversation_messages.sort(key=lambda m: m.get("info", {}).get("created_at", 0))
        
        print(f"  Found {len(conversation_messages)} messages")
        
        # Build the nested structure
        result = {
            "id": conversation["id"],
            "info": conversation.get("info", {}),
            "metadata": conversation.get("metadata", {}),
            "object": conversation.get("object", {}),
            "partition_id": conversation.get("partition_id", ""),
            "messages": conversation_messages,  # Always include messages
            "conv2item": None,
            "_rid": conversation.get("_rid", ""),
            "_self": conversation.get("_self", ""),
            "_etag": conversation.get("_etag", ""),
            "_attachments": conversation.get("_attachments", ""),
            "_ts": conversation.get("_ts", 0)
        }
        
        # Add conv2item edge if found
        if edge_doc:
            result["conv2item"] = {
                "id": edge_doc["id"],
                "info": edge_doc.get("info", {}),
                "metadata": edge_doc.get("metadata", {}),
                "object": edge_doc.get("object", {}),
                "partition_id": edge_doc.get("partition_id", ""),
                "_rid": edge_doc.get("_rid", ""),
                "_self": edge_doc.get("_self", ""),
                "_etag": edge_doc.get("_etag", ""),
                "_attachments": edge_doc.get("_attachments", ""),
                "_ts": edge_doc.get("_ts", 0)
            }
        
        results.append(result)
    
    return results


def main():
    """Example usage"""
    import json
    
    # Fetch all conversations for a specific user
    results = get_all_conversations_by_metadata(
        uid="378789217893712",
        verified=True
    )
    
    print(f"\n{'='*80}")
    print(f"Total conversations found: {len(results)}")
    print(f"{'='*80}\n")
    
    # Print summary of each conversation
    for i, conv in enumerate(results, 1):
        message_count = len(conv.get("messages", []))
        print(f"{i}. Conversation: {conv['id']}")
        print(f"   Created: {conv.get('info', {}).get('created_at', 'N/A')}")
        print(f"   Messages: {message_count}")
        print(f"   Partition: {conv.get('partition_id', 'N/A')}")
        print()
    
    # Save to file
    output_file = "/home/deep/dev/foundry-devops/scripts/runtime/conversations_output.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\nFull results saved to: {output_file}")


if __name__ == "__main__":
    main()
