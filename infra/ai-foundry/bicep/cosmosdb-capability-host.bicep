// Parameters
param aiFoundryName string
param aiProjectName string
param location string
param cosmosDbAccountName string = '${toLower(aiProjectName)}-threads-cosmos'
param cosmosDbDatabaseName string = 'ThreadStorage'
param aiSearchName string = '${toLower(aiProjectName)}-aisearch'
param storageAccountName string = 'adevlabdeep1731'
param storageAccountResourceGroup string = 'devtestlab'

// CosmosDB Account for Thread Storage
// Must have at least 3000 RU/s for AI Foundry thread storage
resource cosmosDbAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: cosmosDbAccountName
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    enableAutomaticFailover: false
    enableMultipleWriteLocations: false
    // Ensure minimum throughput for thread storage
    capabilities: []
  }
}

// Azure AI Search for Vector Store
resource aiSearch 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: aiSearchName
  location: location
  sku: {
    name: 'basic' // Basic tier is sufficient for most scenarios
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'enabled'
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
  }
  identity: {
    type: 'SystemAssigned'
  }
}

// CosmosDB SQL Database
resource cosmosDbDatabase 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmosDbAccount
  name: cosmosDbDatabaseName
  properties: {
    resource: {
      id: cosmosDbDatabaseName
    }
    options: {
      throughput: 3000  // Minimum required for thread storage
    }
  }
}

// Thread Message Store Container
resource threadMessageStoreContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: cosmosDbDatabase
  name: 'thread-message-store'
  properties: {
    resource: {
      id: 'thread-message-store'
      partitionKey: {
        paths: ['/threadId']
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          {
            path: '/*'
          }
        ]
      }
    }
  }
}

// Agent Entity Container
resource agentEntityContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: cosmosDbDatabase
  name: 'agent-entity'
  properties: {
    resource: {
      id: 'agent-entity'
      partitionKey: {
        paths: ['/id']
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          {
            path: '/*'
          }
        ]
      }
    }
  }
}

// Get reference to existing AI Foundry project
resource aiFoundry 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: aiFoundryName
}

resource aiProject 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  name: aiProjectName
  parent: aiFoundry
}

// Get reference to existing Storage Account
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
  scope: resourceGroup(storageAccountResourceGroup)
}

// Create CosmosDB connection in the project
resource cosmosDbConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: aiProject
  name: 'cosmosdb-thread-storage'
  properties: {
    category: 'CosmosDB'
    target: cosmosDbAccount.properties.documentEndpoint
    authType: 'AAD'
    metadata: {
      databaseName: cosmosDbDatabaseName
      containerName: 'thread-message-store'
    }
  }
}

// Create AI Search connection in the project
resource aiSearchConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: aiProject
  name: 'aisearch-vector-store'
  properties: {
    category: 'CognitiveSearch'
    target: 'https://${aiSearch.name}.search.windows.net'
    authType: 'AAD'
    metadata: {}
  }
}

// Create Storage Account connection with AAD authentication
resource storageConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: aiProject
  name: 'storage-aad-connection'
  properties: {
    category: 'AzureStorageAccount'
    target: 'https://${storageAccount.name}.blob.core.windows.net/'
    authType: 'AAD'
    metadata: {
      ResourceId: storageAccount.id
    }
  }
}

// Create Project Capability Host with all required connections
resource projectCapabilityHost 'Microsoft.CognitiveServices/accounts/projects/capabilityHosts@2025-04-01-preview' = {
  parent: aiProject
  name: 'default'
  properties: {
    threadStorageConnections: [cosmosDbConnection.name]
    storageConnections: [storageConnection.name]
    vectorStoreConnections: [aiSearchConnection.name]
  }
}

// RBAC: Assign Cosmos DB Built-in Data Contributor role to project identity
resource cosmosRoleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmosDbAccount
  name: guid(aiProject.id, cosmosDbAccount.id, 'contributor')
  properties: {
    roleDefinitionId: '${cosmosDbAccount.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    principalId: aiProject.identity.principalId
    scope: cosmosDbAccount.id
  }
}

// RBAC: Assign Search Index Data Contributor role to project identity
resource searchRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: aiSearch
  name: guid(aiProject.id, aiSearch.id, 'searchContributor')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7') // Search Index Data Contributor
    principalId: aiProject.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Note: Storage RBAC assignment is in a different resource group (devtestlab)
// It was already assigned manually via az role assignment create command

// Outputs
output cosmosDbAccountId string = cosmosDbAccount.id
output cosmosDbAccountName string = cosmosDbAccount.name
output cosmosDbEndpoint string = cosmosDbAccount.properties.documentEndpoint
output cosmosDbDatabaseName string = cosmosDbDatabaseName
output aiSearchName string = aiSearch.name
output aiSearchEndpoint string = 'https://${aiSearch.name}.search.windows.net'
output connectionName string = cosmosDbConnection.name
output aiSearchConnectionName string = aiSearchConnection.name
output storageConnectionName string = storageConnection.name
output capabilityHostName string = projectCapabilityHost.name
output capabilityHostStatus string = projectCapabilityHost.properties.provisioningState
