## Azure AI Foundry — Multi-Scenario Terraform Configuration
##
## Supports all 4 combinations via variable toggles:
##   1. Private Networking + Capability Host   (enable_private_networking=true,  enable_capability_host=true)
##   2. Private Networking + No Capability Host (enable_private_networking=true,  enable_capability_host=false)
##   3. Public Networking  + Capability Host   (enable_private_networking=false, enable_capability_host=true)
##   4. Public Networking  + No Capability Host (enable_private_networking=false, enable_capability_host=false)

resource "random_string" "unique" {
  length      = 5
  min_numeric = 5
  numeric     = true
  special     = false
  lower       = true
  upper       = false
}

locals {
  suffix      = random_string.unique.result
  private_cap = var.enable_private_networking && var.enable_capability_host
}

## =====================================================================
## CORE RESOURCES (all scenarios)
## =====================================================================

resource "azapi_resource" "rg" {
  type     = "Microsoft.Resources/resourceGroups@2021-04-01"
  name     = "rg-aifoundry-${var.scenario_name}-${local.suffix}"
  location = var.location
}

resource "azapi_resource" "ai_foundry" {
  type                      = "Microsoft.CognitiveServices/accounts@2025-06-01"
  name                      = "aifoundry${local.suffix}"
  parent_id                 = azapi_resource.rg.id
  location                  = var.location
  schema_validation_enabled = false
  response_export_values    = ["identity.principalId"]

  body = {
    kind = "AIServices"
    sku = {
      name = "S0"
    }
    identity = {
      type = "SystemAssigned"
    }
    properties = {
      disableLocalAuth       = false
      allowProjectManagement = true
      customSubDomainName    = "aifoundry${local.suffix}"
      publicNetworkAccess    = var.enable_private_networking ? "Disabled" : "Enabled"
      networkAcls = {
        defaultAction       = var.enable_private_networking ? "Deny" : "Allow"
        ipRules             = []
        virtualNetworkRules = []
      }
    }
  }
}

resource "azapi_resource" "aifoundry_deployment_gpt_4o" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2023-05-01"
  name      = "gpt-4o"
  parent_id = azapi_resource.ai_foundry.id
  depends_on = [
    azapi_resource.ai_foundry
  ]

  body = {
    sku = {
      name     = "GlobalStandard"
      capacity = 1
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = "gpt-4o"
        version = "2024-11-20"
      }
    }
  }
}

resource "azapi_resource" "ai_foundry_project" {
  type                      = "Microsoft.CognitiveServices/accounts/projects@2025-06-01"
  name                      = "project${local.suffix}"
  parent_id                 = azapi_resource.ai_foundry.id
  location                  = var.location
  schema_validation_enabled = false
  response_export_values    = ["identity.principalId"]

  body = {
    sku = {
      name = "S0"
    }
    identity = {
      type = "SystemAssigned"
    }
    properties = {
      displayName = "project-${var.scenario_name}"
      description = "AI Foundry project for scenario: ${var.scenario_name}"
    }
  }
}

## =====================================================================
## PRIVATE NETWORKING (enable_private_networking = true)
## =====================================================================

resource "azapi_resource" "vnet" {
  count     = var.enable_private_networking ? 1 : 0
  type      = "Microsoft.Network/virtualNetworks@2023-04-01"
  name      = "vnet-aifoundry-${local.suffix}"
  parent_id = azapi_resource.rg.id
  location  = var.location

  body = {
    properties = {
      addressSpace = {
        addressPrefixes = ["10.0.0.0/16"]
      }
      subnets = [
        {
          name = "private-endpoints"
          properties = {
            addressPrefix                     = "10.0.1.0/24"
            privateEndpointNetworkPolicies    = "Disabled"
            privateLinkServiceNetworkPolicies = "Disabled"
          }
        }
      ]
    }
  }
}

resource "azapi_resource" "dns_zone_cognitiveservices" {
  count     = var.enable_private_networking ? 1 : 0
  type      = "Microsoft.Network/privateDnsZones@2020-06-01"
  name      = "privatelink.cognitiveservices.azure.com"
  parent_id = azapi_resource.rg.id
  location  = "global"
  body      = {}
}

resource "azapi_resource" "dns_zone_vnet_link_cognitiveservices" {
  count     = var.enable_private_networking ? 1 : 0
  type      = "Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01"
  name      = "link-cognitiveservices-${local.suffix}"
  parent_id = azapi_resource.dns_zone_cognitiveservices[0].id
  location  = "global"

  body = {
    properties = {
      registrationEnabled = false
      virtualNetwork = {
        id = azapi_resource.vnet[0].id
      }
    }
  }
}

# Wait for AI Foundry account to finish provisioning before creating the PE.
# The account may be in "Accepted" state immediately after creation and the
# model deployment — the PE call rejects that.
resource "time_sleep" "wait_for_ai_foundry" {
  count           = var.enable_private_networking ? 1 : 0
  depends_on      = [azapi_resource.aifoundry_deployment_gpt_4o]
  create_duration = "120s"
}

resource "azapi_resource" "ai_foundry_pe" {
  count      = var.enable_private_networking ? 1 : 0
  type       = "Microsoft.Network/privateEndpoints@2023-04-01"
  name       = "pe-aifoundry-${local.suffix}"
  parent_id  = azapi_resource.rg.id
  location   = var.location
  depends_on = [time_sleep.wait_for_ai_foundry]

  body = {
    properties = {
      subnet = {
        id = "${azapi_resource.vnet[0].id}/subnets/private-endpoints"
      }
      privateLinkServiceConnections = [
        {
          name = "aifoundry-connection"
          properties = {
            privateLinkServiceId = azapi_resource.ai_foundry.id
            groupIds             = ["account"]
          }
        }
      ]
    }
  }
}

resource "azapi_resource" "ai_foundry_pe_dns_group" {
  count     = var.enable_private_networking ? 1 : 0
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-04-01"
  name      = "default"
  parent_id = azapi_resource.ai_foundry_pe[0].id

  body = {
    properties = {
      privateDnsZoneConfigs = [
        {
          name = "cognitiveservices"
          properties = {
            privateDnsZoneId = azapi_resource.dns_zone_cognitiveservices[0].id
          }
        }
      ]
    }
  }
}

## =====================================================================
## CAPABILITY HOST RESOURCES (enable_capability_host = true)
## =====================================================================

resource "random_uuid" "cosmos_role_assignment_id" {
  count = var.enable_capability_host ? 1 : 0
}

resource "random_uuid" "search_role_assignment_id" {
  count = var.enable_capability_host ? 1 : 0
}

resource "random_uuid" "storage_role_assignment_id" {
  count = var.enable_capability_host ? 1 : 0
}

resource "random_uuid" "acct_cosmos_role_assignment_id" {
  count = var.enable_capability_host ? 1 : 0
}

resource "random_uuid" "acct_search_role_assignment_id" {
  count = var.enable_capability_host ? 1 : 0
}

resource "random_uuid" "acct_storage_role_assignment_id" {
  count = var.enable_capability_host ? 1 : 0
}

resource "random_uuid" "acct_search_svc_role_assignment_id" {
  count = var.enable_capability_host ? 1 : 0
}

resource "azapi_resource" "storage_account" {
  count     = var.enable_capability_host ? 1 : 0
  type      = "Microsoft.Storage/storageAccounts@2023-05-01"
  name      = "st${local.suffix}"
  parent_id = azapi_resource.rg.id
  location  = var.location

  body = {
    sku = {
      name = "Standard_LRS"
    }
    kind = "StorageV2"
    properties = {
      accessTier            = "Hot"
      allowBlobPublicAccess = false
      minimumTlsVersion     = "TLS1_2"
      publicNetworkAccess   = var.enable_private_networking ? "Disabled" : "Enabled"
    }
  }
}

resource "azapi_resource" "cosmosdb_account" {
  count                  = var.enable_capability_host ? 1 : 0
  type                   = "Microsoft.DocumentDB/databaseAccounts@2024-05-15"
  name                   = "cosmos${local.suffix}"
  parent_id              = azapi_resource.rg.id
  location               = var.location
  response_export_values = ["properties.documentEndpoint"]

  body = {
    kind = "GlobalDocumentDB"
    properties = {
      databaseAccountOfferType = "Standard"
      consistencyPolicy = {
        defaultConsistencyLevel = "Session"
      }
      locations = [
        {
          locationName     = var.location
          failoverPriority = 0
          isZoneRedundant  = false
        }
      ]
      enableAutomaticFailover      = false
      enableMultipleWriteLocations = false
      disableLocalAuth             = false
      publicNetworkAccess          = var.enable_private_networking ? "Disabled" : "Enabled"
    }
  }
}

resource "azapi_resource" "cosmosdb_database" {
  count     = var.enable_capability_host ? 1 : 0
  type      = "Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15"
  name      = "ThreadStorage"
  parent_id = azapi_resource.cosmosdb_account[0].id

  body = {
    properties = {
      resource = {
        id = "ThreadStorage"
      }
    }
  }
}

resource "azapi_resource" "ai_search" {
  count                     = var.enable_capability_host ? 1 : 0
  type                      = "Microsoft.Search/searchServices@2024-06-01-preview"
  name                      = "search${local.suffix}"
  parent_id                 = azapi_resource.rg.id
  location                  = var.location
  schema_validation_enabled = false

  body = {
    sku = {
      name = "standard"
    }
    properties = {
      replicaCount        = 1
      partitionCount      = 1
      hostingMode         = "default"
      publicNetworkAccess = var.enable_private_networking ? "disabled" : "enabled"
      authOptions = {
        aadOrApiKey = {
          aadAuthFailureMode = "http401WithBearerChallenge"
        }
      }
    }
  }
}

resource "azapi_resource" "cosmosdb_connection" {
  count     = var.enable_capability_host ? 1 : 0
  type      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview"
  name      = "cosmosdb-thread-storage"
  parent_id = azapi_resource.ai_foundry_project.id
  depends_on = [
    azapi_resource.cosmosdb_account,
    azapi_resource.cosmosdb_database,
  ]

  body = {
    properties = {
      category = "CosmosDB"
      target   = azapi_resource.cosmosdb_account[0].output.properties.documentEndpoint
      authType = "AAD"
      metadata = {
        databaseName  = "ThreadStorage"
        containerName = "thread-message-store"
        ResourceId    = azapi_resource.cosmosdb_account[0].id
      }
    }
  }
}

resource "azapi_resource" "aisearch_connection" {
  count      = var.enable_capability_host ? 1 : 0
  type       = "Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview"
  name       = "aisearch-vector-store"
  parent_id  = azapi_resource.ai_foundry_project.id
  depends_on = [azapi_resource.ai_search]

  body = {
    properties = {
      category = "CognitiveSearch"
      target   = "https://${azapi_resource.ai_search[0].name}.search.windows.net"
      authType = "AAD"
      metadata = {
        ResourceId = azapi_resource.ai_search[0].id
      }
    }
  }
}

resource "azapi_resource" "storage_connection" {
  count      = var.enable_capability_host ? 1 : 0
  type       = "Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview"
  name       = "storage-aad-connection"
  parent_id  = azapi_resource.ai_foundry_project.id
  depends_on = [azapi_resource.storage_account]

  body = {
    properties = {
      category = "AzureStorageAccount"
      target   = "https://${azapi_resource.storage_account[0].name}.blob.core.windows.net/"
      authType = "AAD"
      metadata = {
        ResourceId = azapi_resource.storage_account[0].id
      }
    }
  }
}

## Account-level Capability Host (must exist before project-level)
resource "azapi_resource" "account_capability_host" {
  count                     = var.enable_capability_host ? 1 : 0
  type                      = "Microsoft.CognitiveServices/accounts/capabilityHosts@2025-04-01-preview"
  name                      = "default"
  parent_id                 = azapi_resource.ai_foundry.id
  schema_validation_enabled = false
  depends_on = [
    azapi_resource.cosmosdb_connection,
    azapi_resource.aisearch_connection,
    azapi_resource.storage_connection,
    azapi_resource.acct_cosmos_role_assignment,
    azapi_resource.acct_search_role_assignment,
    azapi_resource.acct_search_service_role_assignment,
    azapi_resource.acct_storage_role_assignment,
  ]

  body = {
    properties = {
      threadStorageConnections = [azapi_resource.cosmosdb_connection[0].name]
      storageConnections       = [azapi_resource.storage_connection[0].name]
      vectorStoreConnections   = [azapi_resource.aisearch_connection[0].name]
    }
  }
}

## Project-level Capability Host
resource "azapi_resource" "capability_host" {
  count                     = var.enable_capability_host ? 1 : 0
  type                      = "Microsoft.CognitiveServices/accounts/projects/capabilityHosts@2025-04-01-preview"
  name                      = "default"
  parent_id                 = azapi_resource.ai_foundry_project.id
  schema_validation_enabled = false
  depends_on = [
    azapi_resource.account_capability_host,
    azapi_resource.cosmosdb_connection,
    azapi_resource.aisearch_connection,
    azapi_resource.storage_connection,
    azapi_resource.cosmos_role_assignment,
    azapi_resource.search_role_assignment,
    azapi_resource.storage_role_assignment,
    azapi_resource.acct_cosmos_role_assignment,
    azapi_resource.acct_search_role_assignment,
    azapi_resource.acct_search_service_role_assignment,
    azapi_resource.acct_storage_role_assignment,
  ]

  body = {
    properties = {
      threadStorageConnections = [azapi_resource.cosmosdb_connection[0].name]
      storageConnections       = [azapi_resource.storage_connection[0].name]
      vectorStoreConnections   = [azapi_resource.aisearch_connection[0].name]
    }
  }
}

## RBAC: CosmosDB Built-in Data Contributor → AI Project managed identity
resource "azapi_resource" "cosmos_role_assignment" {
  count     = var.enable_capability_host ? 1 : 0
  type      = "Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15"
  name      = random_uuid.cosmos_role_assignment_id[0].result
  parent_id = azapi_resource.cosmosdb_account[0].id

  body = {
    properties = {
      roleDefinitionId = "${azapi_resource.cosmosdb_account[0].id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
      principalId      = azapi_resource.ai_foundry_project.output.identity.principalId
      scope            = azapi_resource.cosmosdb_account[0].id
    }
  }
}

## RBAC: Search Index Data Contributor → AI Project managed identity
resource "azapi_resource" "search_role_assignment" {
  count     = var.enable_capability_host ? 1 : 0
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = random_uuid.search_role_assignment_id[0].result
  parent_id = azapi_resource.ai_search[0].id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/8ebe5a00-799e-43f5-93ac-243d3dce84a7"
      principalId      = azapi_resource.ai_foundry_project.output.identity.principalId
      principalType    = "ServicePrincipal"
    }
  }
}

## RBAC: Storage Blob Data Contributor → AI Project managed identity
resource "azapi_resource" "storage_role_assignment" {
  count     = var.enable_capability_host ? 1 : 0
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = random_uuid.storage_role_assignment_id[0].result
  parent_id = azapi_resource.storage_account[0].id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"
      principalId      = azapi_resource.ai_foundry_project.output.identity.principalId
      principalType    = "ServicePrincipal"
    }
  }
}

## RBAC: CosmosDB Built-in Data Contributor → AI Foundry account managed identity
resource "azapi_resource" "acct_cosmos_role_assignment" {
  count     = var.enable_capability_host ? 1 : 0
  type      = "Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15"
  name      = random_uuid.acct_cosmos_role_assignment_id[0].result
  parent_id = azapi_resource.cosmosdb_account[0].id

  body = {
    properties = {
      roleDefinitionId = "${azapi_resource.cosmosdb_account[0].id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
      principalId      = azapi_resource.ai_foundry.output.identity.principalId
      scope            = azapi_resource.cosmosdb_account[0].id
    }
  }
}

## RBAC: Search Index Data Contributor → AI Foundry account managed identity
resource "azapi_resource" "acct_search_role_assignment" {
  count     = var.enable_capability_host ? 1 : 0
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = random_uuid.acct_search_role_assignment_id[0].result
  parent_id = azapi_resource.ai_search[0].id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/8ebe5a00-799e-43f5-93ac-243d3dce84a7"
      principalId      = azapi_resource.ai_foundry.output.identity.principalId
      principalType    = "ServicePrincipal"
    }
  }
}

## RBAC: Search Service Contributor → AI Foundry account managed identity
resource "azapi_resource" "acct_search_service_role_assignment" {
  count     = var.enable_capability_host ? 1 : 0
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = random_uuid.acct_search_svc_role_assignment_id[0].result
  parent_id = azapi_resource.ai_search[0].id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/7ca78c08-252a-4471-8644-bb5ff32d4ba0"
      principalId      = azapi_resource.ai_foundry.output.identity.principalId
      principalType    = "ServicePrincipal"
    }
  }
}

## RBAC: Storage Blob Data Contributor → AI Foundry account managed identity
resource "azapi_resource" "acct_storage_role_assignment" {
  count     = var.enable_capability_host ? 1 : 0
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = random_uuid.acct_storage_role_assignment_id[0].result
  parent_id = azapi_resource.storage_account[0].id

  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"
      principalId      = azapi_resource.ai_foundry.output.identity.principalId
      principalType    = "ServicePrincipal"
    }
  }
}

## =====================================================================
## PRIVATE NETWORKING FOR CAPABILITY HOST RESOURCES
## (enable_private_networking = true AND enable_capability_host = true)
## =====================================================================

## --- CosmosDB ---

resource "azapi_resource" "dns_zone_cosmosdb" {
  count     = local.private_cap ? 1 : 0
  type      = "Microsoft.Network/privateDnsZones@2020-06-01"
  name      = "privatelink.documents.azure.com"
  parent_id = azapi_resource.rg.id
  location  = "global"
  body      = {}
}

resource "azapi_resource" "dns_zone_vnet_link_cosmosdb" {
  count     = local.private_cap ? 1 : 0
  type      = "Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01"
  name      = "link-cosmosdb-${local.suffix}"
  parent_id = azapi_resource.dns_zone_cosmosdb[0].id
  location  = "global"

  body = {
    properties = {
      registrationEnabled = false
      virtualNetwork = {
        id = azapi_resource.vnet[0].id
      }
    }
  }
}

resource "azapi_resource" "cosmosdb_pe" {
  count      = local.private_cap ? 1 : 0
  type       = "Microsoft.Network/privateEndpoints@2023-04-01"
  name       = "pe-cosmos-${local.suffix}"
  parent_id  = azapi_resource.rg.id
  location   = var.location
  depends_on = [azapi_resource.cosmosdb_account]

  body = {
    properties = {
      subnet = {
        id = "${azapi_resource.vnet[0].id}/subnets/private-endpoints"
      }
      privateLinkServiceConnections = [
        {
          name = "cosmosdb-connection"
          properties = {
            privateLinkServiceId = azapi_resource.cosmosdb_account[0].id
            groupIds             = ["Sql"]
          }
        }
      ]
    }
  }
}

resource "azapi_resource" "cosmosdb_pe_dns_group" {
  count     = local.private_cap ? 1 : 0
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-04-01"
  name      = "default"
  parent_id = azapi_resource.cosmosdb_pe[0].id

  body = {
    properties = {
      privateDnsZoneConfigs = [
        {
          name = "cosmosdb"
          properties = {
            privateDnsZoneId = azapi_resource.dns_zone_cosmosdb[0].id
          }
        }
      ]
    }
  }
}

## --- AI Search ---

resource "azapi_resource" "dns_zone_aisearch" {
  count     = local.private_cap ? 1 : 0
  type      = "Microsoft.Network/privateDnsZones@2020-06-01"
  name      = "privatelink.search.windows.net"
  parent_id = azapi_resource.rg.id
  location  = "global"
  body      = {}
}

resource "azapi_resource" "dns_zone_vnet_link_aisearch" {
  count     = local.private_cap ? 1 : 0
  type      = "Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01"
  name      = "link-aisearch-${local.suffix}"
  parent_id = azapi_resource.dns_zone_aisearch[0].id
  location  = "global"

  body = {
    properties = {
      registrationEnabled = false
      virtualNetwork = {
        id = azapi_resource.vnet[0].id
      }
    }
  }
}

resource "azapi_resource" "aisearch_pe" {
  count      = local.private_cap ? 1 : 0
  type       = "Microsoft.Network/privateEndpoints@2023-04-01"
  name       = "pe-search-${local.suffix}"
  parent_id  = azapi_resource.rg.id
  location   = var.location
  depends_on = [azapi_resource.ai_search]

  body = {
    properties = {
      subnet = {
        id = "${azapi_resource.vnet[0].id}/subnets/private-endpoints"
      }
      privateLinkServiceConnections = [
        {
          name = "aisearch-connection"
          properties = {
            privateLinkServiceId = azapi_resource.ai_search[0].id
            groupIds             = ["searchService"]
          }
        }
      ]
    }
  }
}

resource "azapi_resource" "aisearch_pe_dns_group" {
  count     = local.private_cap ? 1 : 0
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-04-01"
  name      = "default"
  parent_id = azapi_resource.aisearch_pe[0].id

  body = {
    properties = {
      privateDnsZoneConfigs = [
        {
          name = "aisearch"
          properties = {
            privateDnsZoneId = azapi_resource.dns_zone_aisearch[0].id
          }
        }
      ]
    }
  }
}

## --- Storage ---

resource "azapi_resource" "dns_zone_storage" {
  count     = local.private_cap ? 1 : 0
  type      = "Microsoft.Network/privateDnsZones@2020-06-01"
  name      = "privatelink.blob.core.windows.net"
  parent_id = azapi_resource.rg.id
  location  = "global"
  body      = {}
}

resource "azapi_resource" "dns_zone_vnet_link_storage" {
  count     = local.private_cap ? 1 : 0
  type      = "Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01"
  name      = "link-storage-${local.suffix}"
  parent_id = azapi_resource.dns_zone_storage[0].id
  location  = "global"

  body = {
    properties = {
      registrationEnabled = false
      virtualNetwork = {
        id = azapi_resource.vnet[0].id
      }
    }
  }
}

resource "azapi_resource" "storage_pe" {
  count      = local.private_cap ? 1 : 0
  type       = "Microsoft.Network/privateEndpoints@2023-04-01"
  name       = "pe-storage-${local.suffix}"
  parent_id  = azapi_resource.rg.id
  location   = var.location
  depends_on = [azapi_resource.storage_account]

  body = {
    properties = {
      subnet = {
        id = "${azapi_resource.vnet[0].id}/subnets/private-endpoints"
      }
      privateLinkServiceConnections = [
        {
          name = "storage-connection"
          properties = {
            privateLinkServiceId = azapi_resource.storage_account[0].id
            groupIds             = ["blob"]
          }
        }
      ]
    }
  }
}

resource "azapi_resource" "storage_pe_dns_group" {
  count     = local.private_cap ? 1 : 0
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-04-01"
  name      = "default"
  parent_id = azapi_resource.storage_pe[0].id

  body = {
    properties = {
      privateDnsZoneConfigs = [
        {
          name = "storage-blob"
          properties = {
            privateDnsZoneId = azapi_resource.dns_zone_storage[0].id
          }
        }
      ]
    }
  }
}