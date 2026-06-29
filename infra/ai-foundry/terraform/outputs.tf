output "scenario_name" {
  description = "The scenario deployed"
  value       = var.scenario_name
}

output "resource_group_name" {
  description = "Name of the resource group"
  value       = azapi_resource.rg.name
}

output "ai_foundry_name" {
  description = "Name of the AI Foundry resource"
  value       = azapi_resource.ai_foundry.name
}

output "ai_foundry_endpoint" {
  description = "Endpoint URL for the AI Foundry resource"
  value       = "https://${azapi_resource.ai_foundry.name}.cognitiveservices.azure.com/"
}

output "ai_foundry_project_name" {
  description = "Name of the AI Foundry project"
  value       = azapi_resource.ai_foundry_project.name
}

output "networking_mode" {
  description = "Networking mode: private or public"
  value       = var.enable_private_networking ? "private" : "public"
}

output "capability_host_enabled" {
  description = "Whether Capability Host was deployed"
  value       = var.enable_capability_host
}

output "vnet_name" {
  description = "Name of the VNet (private networking only)"
  value       = var.enable_private_networking ? azapi_resource.vnet[0].name : "N/A (public networking)"
}

output "cosmosdb_account_name" {
  description = "Name of the CosmosDB account (capability host only)"
  value       = var.enable_capability_host ? azapi_resource.cosmosdb_account[0].name : "N/A (no capability host)"
}

output "cosmosdb_endpoint" {
  description = "CosmosDB endpoint (capability host only)"
  value       = var.enable_capability_host ? azapi_resource.cosmosdb_account[0].output.properties.documentEndpoint : "N/A (no capability host)"
}

output "ai_search_name" {
  description = "Name of the AI Search service (capability host only)"
  value       = var.enable_capability_host ? azapi_resource.ai_search[0].name : "N/A (no capability host)"
}

output "ai_search_endpoint" {
  description = "AI Search endpoint (capability host only)"
  value       = var.enable_capability_host ? "https://${azapi_resource.ai_search[0].name}.search.windows.net" : "N/A (no capability host)"
}

output "storage_account_name" {
  description = "Name of the Storage Account (capability host only)"
  value       = var.enable_capability_host ? azapi_resource.storage_account[0].name : "N/A (no capability host)"
}

output "capability_host_name" {
  description = "Name of the Capability Host resource (capability host only)"
  value       = var.enable_capability_host ? azapi_resource.capability_host[0].name : "N/A (no capability host)"
}
