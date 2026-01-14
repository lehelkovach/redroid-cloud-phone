# Terraform Variables for Cloud Phone Deployment

# =============================================================================
# OCI Authentication
# =============================================================================

variable "tenancy_ocid" {
  description = "OCI tenancy OCID"
  type        = string
}

variable "user_ocid" {
  description = "OCI user OCID"
  type        = string
}

variable "fingerprint" {
  description = "OCI API key fingerprint"
  type        = string
}

variable "private_key_path" {
  description = "Path to OCI API private key"
  type        = string
}

variable "region" {
  description = "OCI region"
  type        = string
  default     = "us-phoenix-1"
}

# =============================================================================
# Instance Configuration
# =============================================================================

variable "compartment_ocid" {
  description = "OCI compartment OCID"
  type        = string
}

variable "subnet_ocid" {
  description = "OCI subnet OCID"
  type        = string
}

variable "instance_name" {
  description = "Instance display name"
  type        = string
  default     = "cloud-phone"
}

variable "instance_count" {
  description = "Number of instances to create"
  type        = number
  default     = 1
}

variable "instance_shape" {
  description = "Instance shape"
  type        = string
  default     = "VM.Standard.A1.Flex"
}

variable "instance_ocpus" {
  description = "Number of OCPUs"
  type        = number
  default     = 2

  validation {
    condition     = var.instance_ocpus >= 1 && var.instance_ocpus <= 4
    error_message = "OCPUs must be between 1 and 4 for Always Free tier."
  }
}

variable "instance_memory_gb" {
  description = "Memory in GB"
  type        = number
  default     = 8

  validation {
    condition     = var.instance_memory_gb >= 1 && var.instance_memory_gb <= 24
    error_message = "Memory must be between 1 and 24 GB for Always Free tier."
  }
}

variable "ubuntu_version" {
  description = "Ubuntu version (20.04 recommended for virtual devices)"
  type        = string
  default     = "20.04"
}

variable "golden_image_ocid" {
  description = "Golden image OCID (if using pre-built image)"
  type        = string
  default     = ""
}

# =============================================================================
# SSH Configuration
# =============================================================================

variable "ssh_public_key" {
  description = "SSH public key content"
  type        = string
}

variable "ssh_private_key_path" {
  description = "Path to SSH private key (for output commands)"
  type        = string
  default     = "~/.ssh/id_rsa"
}

# =============================================================================
# Network Configuration
# =============================================================================

variable "assign_public_ip" {
  description = "Assign public IP to instance"
  type        = bool
  default     = true
}

# =============================================================================
# Proxy Configuration
# =============================================================================

variable "proxy_enabled" {
  description = "Enable proxy configuration"
  type        = bool
  default     = false
}

variable "proxy_type" {
  description = "Proxy type (http, socks5)"
  type        = string
  default     = "socks5"
}

variable "proxy_host" {
  description = "Proxy host"
  type        = string
  default     = ""
}

variable "proxy_port" {
  description = "Proxy port"
  type        = number
  default     = 1080
}

variable "proxy_username" {
  description = "Proxy username (optional)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "proxy_password" {
  description = "Proxy password (optional)"
  type        = string
  default     = ""
  sensitive   = true
}

# =============================================================================
# GPS Configuration
# =============================================================================

variable "gps_enabled" {
  description = "Enable GPS spoofing"
  type        = bool
  default     = false
}

variable "gps_latitude" {
  description = "GPS latitude"
  type        = number
  default     = 0
}

variable "gps_longitude" {
  description = "GPS longitude"
  type        = number
  default     = 0
}

# =============================================================================
# Source Configuration
# =============================================================================

variable "github_repo" {
  description = "GitHub repository for fresh install"
  type        = string
  default     = "lehelkovach/redroid-cloud-phone"
}

# =============================================================================
# Other
# =============================================================================

variable "preserve_boot_volume" {
  description = "Preserve boot volume on termination"
  type        = bool
  default     = false
}

variable "tags" {
  description = "Freeform tags"
  type        = map(string)
  default     = {}
}
