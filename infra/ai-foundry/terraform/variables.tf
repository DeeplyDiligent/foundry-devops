variable "location" {
  description = "The Azure region to deploy resources to"
  type        = string
  default     = "swedencentral"
}

variable "subscription_id" {
  description = "The Azure subscription ID"
  type        = string
}

variable "scenario_name" {
  description = "Short name for this deployment scenario (used in resource group name)"
  type        = string
  default     = "default"

  validation {
    condition     = length(var.scenario_name) <= 20 && can(regex("^[a-z0-9-]+$", var.scenario_name))
    error_message = "scenario_name must be lowercase alphanumeric and hyphens, max 20 chars."
  }
}

variable "enable_private_networking" {
  description = "Deploy with private networking: VNet, Private Endpoints, and Private DNS Zones"
  type        = bool
  default     = false
}

variable "enable_capability_host" {
  description = "Deploy Capability Host with CosmosDB thread storage, AI Search vector store, and Storage connections"
  type        = bool
  default     = false
}