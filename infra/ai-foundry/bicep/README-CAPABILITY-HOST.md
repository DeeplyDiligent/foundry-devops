# Azure AI Foundry Capability Host with CosmosDB Thread Storage

## Overview
This setup configures an Azure AI Foundry project with **Standard Agent Setup** using your own Azure resources for complete data sovereignty and control.

## What Was Created

### 1. CosmosDB Account and Database
- **CosmosDB Account**: `swe-proj-threads-cosmos`
- **Database**: `ThreadStorage` 
- **Throughput**: 3000 RU/s (minimum required for AI Foundry thread storage)
- **Location**: Sweden Central
- **Containers**:
  - `thread-message-store` - Stores conversation threads and messages (partitioned by `/threadId`)
  - `agent-entity` - Stores agent definitions and metadata (partitioned by `/id`)

### 2. Azure AI Search Service
- **Service Name**: `swe-proj-aisearch`
- **SKU**: Basic
- **Location**: Sweden Central
- **Purpose**: Vector store for embeddings and retrieval operations

### 3. AI Foundry Connections
- **CosmosDB Connection**: `cosmosdb-thread-storage`
  - Type: Azure Cosmos DB (NoSQL)
  - Authentication: Azure AD (Entra ID)
  - Target: https://swe-proj-threads-cosmos.documents.azure.com:443/

- **AI Search Connection**: `aisearch-vector-store`
  - Type: Azure AI Search
  - Authentication: Azure AD (Entra ID)
  - Target: https://swe-proj-aisearch.search.windows.net

- **Storage Connection**: `adevlabdeep1731462bux` (Existing)
  - Type: Azure Storage Account
  - Authentication: Account Key
  - Target: https://adevlabdeep1731.blob.core.windows.net/

### 4. Capability Hosts
- **Account-Level Capability Host**: Created to enable Azure AI Agent Service
- **Project-Level Capability Host**: Configured with all three connections for Standard Agent Setup
  - Status: Currently in configuration (see troubleshooting below)

### 5. RBAC Permissions
The following permissions have been assigned to the project managed identity:

- **Cosmos DB**: Built-in Data Contributor role
- **AI Search**: Search Index Data Contributor role
- **Storage Account**: Storage Blob Data Contributor role

## Project Information
- **AI Foundry Hub**: sweeden-test
- **Project**: swe-proj
- **Project Identity**: 41ff2c3b-d753-4742-9500-fc3a0fdea233
- **Resource Group**: Dev
- **Subscription**: fce27326-d5b1-4a1e-b77c-5a869a831de1
- **Location**: Sweden Central

## Standard Agent Setup

With Standard Agent Setup, all agents in your project will automatically use:
- **CosmosDB** for thread storage (conversations, agent definitions)
- **Azure Storage** for file uploads and blob storage
- **Azure AI Search** for vector embeddings and retrieval

This gives you complete control and visibility over your agent data within your Azure subscription.

## Current Status & Troubleshooting

### Capability Host Configuration
The project capability host has been created with all three required connections but may show a "Failed" provisioning state. This can occur due to:

1. **Permission Propagation**: Azure AD role assignments can take 5-10 minutes to propagate
2. **Connection Authentication**: The storage connection uses Account Key instead of Azure AD
3. **Resource Validation**: The service validates connectivity to all resources during creation

### Recommended Actions

**Option 1: Wait and Retry**
Role assignments may need time to propagate. After waiting 10-15 minutes, recreate the capability host:

```bash
# Delete the capability host
az rest --method DELETE \
  --url "https://management.azure.com/subscriptions/fce27326-d5b1-4a1e-b77c-5a869a831de1/resourceGroups/Dev/providers/Microsoft.CognitiveServices/accounts/sweeden-test/projects/swe-proj/capabilityHosts/default?api-version=2025-06-01"

# Wait a few seconds
sleep 5

# Recreate with all connections
az rest --method PUT \
  --url "https://management.azure.com/subscriptions/fce27326-d5b1-4a1e-b77c-5a869a831de1/resourceGroups/Dev/providers/Microsoft.CognitiveServices/accounts/sweeden-test/projects/swe-proj/capabilityHosts/default?api-version=2025-06-01" \
  --body '{
    "properties": {
      "threadStorageConnections": ["cosmosdb-thread-storage"],
      "storageConnections": ["adevlabdeep1731462bux"],
      "vectorStoreConnections": ["aisearch-vector-store"]
    }
  }'

# Check status
az rest --method GET \
  --url "https://management.azure.com/subscriptions/fce27326-d5b1-4a1e-b77c-5a869a831de1/resourceGroups/Dev/providers/Microsoft.CognitiveServices/accounts/sweeden-test/projects/swe-proj/capabilityHosts/default?api-version=2025-06-01"
```

**Option 2: Create AAD Storage Connection**
If the Account Key authentication is causing issues, create a new storage connection with Azure AD:

```bash
# This would need to be done through the Azure AI Foundry portal
# Navigate to your project -> Connections -> Add Connection
# Select Azure Storage Account and use Azure AD authentication
```

**Option 3: Use Basic Agent Setup (Current Fallback)**
If Standard Agent Setup continues to fail, the system will fall back to Basic Agent Setup which uses:
- Your CosmosDB connection for threads (when specified in agent configuration)
- Microsoft-managed resources for file storage and vector search

## Files Created

1. **cosmosdb-capability-host.bicep** - Complete infrastructure template including:
   - CosmosDB account, database, and containers
   - Azure AI Search service
   - Project connections
   - Capability host configuration
   - RBAC role assignments

2. **cosmosdb-capability-host.bicepparam** - Parameters file with project-specific values

3. **capability-host.yaml** - Reference configuration file (for manual az ml commands)

## Deployment Commands

### Initial Deployment (Already Completed)

```bash
# Deploy all infrastructure
cd /home/deep/dev/foundry-devops/infra/ai-foundry/bicep

az deployment group create \
  --resource-group "Dev" \
  --template-file cosmosdb-capability-host.bicep \
  --parameters cosmosdb-capability-host.bicepparam \
  --name "standard-agent-setup-deployment"
```

This creates:
- CosmosDB account with required containers
- Azure AI Search service
- Project connections for all three services
- RBAC role assignments

### Manual Capability Host Creation

Due to validation requirements, the capability host may need to be created manually after infrastructure deployment:

```bash
# Ensure account capability host exists
az rest --method PUT \
  --url "https://management.azure.com/subscriptions/fce27326-d5b1-4a1e-b77c-5a869a831de1/resourceGroups/Dev/providers/Microsoft.CognitiveServices/accounts/sweeden-test/capabilityHosts/default?api-version=2025-06-01" \
  --body '{"properties": {"capabilityHostKind": "Agents"}}'

# Wait for permission propagation (10-15 minutes recommended)

# Create project capability host with all connections
az rest --method PUT \
  --url "https://management.azure.com/subscriptions/fce27326-d5b1-4a1e-b77c-5a869a831de1/resourceGroups/Dev/providers/Microsoft.CognitiveServices/accounts/sweeden-test/projects/swe-proj/capabilityHosts/default?api-version=2025-06-01" \
  --body '{
    "properties": {
      "threadStorageConnections": ["cosmosdb-thread-storage"],
      "storageConnections": ["adevlabdeep1731462bux"],
      "vectorStoreConnections": ["aisearch-vector-store"]
    }
  }'
```

## Verification

To verify the complete setup:

```bash
# Check CosmosDB account
az cosmosdb show --name "swe-proj-threads-cosmos" --resource-group "Dev" -o table

# Check AI Search service
az search service show --name "swe-proj-aisearch" --resource-group "Dev" -o table

# Check all connections in project
az rest --method GET \
  --url "https://management.azure.com/subscriptions/fce27326-d5b1-4a1e-b77c-5a869a831de1/resourceGroups/Dev/providers/Microsoft.CognitiveServices/accounts/sweeden-test/projects/swe-proj/connections?api-version=2025-06-01" \
  --query "value[?contains(name, 'cosmos') || contains(name, 'search')].{Name:name, Category:properties.category, Auth:properties.authType, State:properties.provisioningState}" -o table

# Check capability host status
az rest --method GET \
  --url "https://management.azure.com/subscriptions/fce27326-d5b1-4a1e-b77c-5a869a831de1/resourceGroups/Dev/providers/Microsoft.CognitiveServices/accounts/sweeden-test/projects/swe-proj/capabilityHosts/default?api-version=2025-06-01" \
  --query "{Name:name, Status:properties.provisioningState, ThreadStorage:properties.threadStorageConnections, FileStorage:properties.storageConnections, VectorStore:properties.vectorStoreConnections}"

# Check RBAC assignments
az role assignment list \
  --assignee "41ff2c3b-d753-4742-9500-fc3a0fdea233" \
  --query "[].{Resource:scope, Role:roleDefinitionName}" -o table
```

## Testing the Setup

Once the capability host shows `provisioningState: "Succeeded"`, test by creating an agent:

1. Go to Azure AI Foundry portal: https://ai.azure.com
2. Navigate to your project: sweeden-test/swe-proj
3. Create a new agent
4. Run a conversation
5. Verify data appears in:
   - CosmosDB: Check `thread-message-store` container
   - Storage: Check for uploaded files
   - AI Search: Check for vector indexes

## Infrastructure Costs

Estimated monthly costs for the deployed resources:

- **CosmosDB** (3000 RU/s): ~$175/month
- **Azure AI Search** (Basic SKU): ~$75/month
- **Storage Account**: Already existing, minimal additional cost

Total additional cost: ~$250/month

## Resources
- [Azure AI Foundry Capability Hosts Documentation](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/concepts/capability-hosts)
- [Standard Agent Setup Guide](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/concepts/standard-agent-setup)
- [CosmosDB Thread Storage Blog Post](https://devblogs.microsoft.com/cosmosdb/azure-ai-foundry-connection-for-azure-cosmos-db-and-byo-thread-storage-in-azure-ai-agent-service/)
