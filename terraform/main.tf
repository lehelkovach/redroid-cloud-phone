# Terraform Configuration for Cloud Phone Deployment
#
# This configuration deploys cloud phone instances to Oracle Cloud.
#
# Usage:
#   terraform init
#   terraform plan -var-file="my-config.tfvars"
#   terraform apply -var-file="my-config.tfvars"
#
# Required variables in terraform.tfvars:
#   tenancy_ocid
#   user_ocid
#   fingerprint
#   private_key_path
#   compartment_ocid
#   subnet_ocid
#   ssh_public_key

terraform {
  required_version = ">= 1.0.0"
  
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 5.0.0"
    }
  }
}

# OCI Provider Configuration
provider "oci" {
  tenancy_ocid     = var.tenancy_ocid
  user_ocid        = var.user_ocid
  fingerprint      = var.fingerprint
  private_key_path = var.private_key_path
  region           = var.region
}

# Data source: Get availability domains
data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

# Data source: Get Ubuntu 20.04 ARM image
data "oci_core_images" "ubuntu" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = var.ubuntu_version
  shape                    = var.instance_shape
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

# Cloud Phone Instance
resource "oci_core_instance" "cloud_phone" {
  count = var.instance_count

  compartment_id      = var.compartment_ocid
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[0].name
  display_name        = var.instance_count > 1 ? "${var.instance_name}-${count.index + 1}" : var.instance_name
  shape               = var.instance_shape

  shape_config {
    ocpus         = var.instance_ocpus
    memory_in_gbs = var.instance_memory_gb
  }

  source_details {
    source_type = "image"
    source_id   = var.golden_image_ocid != "" ? var.golden_image_ocid : data.oci_core_images.ubuntu.images[0].id
  }

  create_vnic_details {
    subnet_id        = var.subnet_ocid
    assign_public_ip = var.assign_public_ip
    display_name     = "${var.instance_name}-vnic"
  }

  metadata = {
    ssh_authorized_keys = var.ssh_public_key
    user_data           = base64encode(local.cloud_init_script)
  }

  # Preserve boot volume on termination
  preserve_boot_volume = var.preserve_boot_volume

  # Freeform tags
  freeform_tags = merge(var.tags, {
    "cloud-phone" = "true"
    "created-by"  = "terraform"
  })

  lifecycle {
    ignore_changes = [
      source_details[0].source_id,
      metadata["user_data"]
    ]
  }
}

# Cloud-init script
locals {
  cloud_init_script = var.golden_image_ocid != "" ? local.golden_image_init : local.fresh_install_init

  golden_image_init = <<-EOF
    #!/bin/bash
    # Golden image - just start services
    systemctl start docker
    sleep 5
    systemctl start redroid-cloud-phone.target
    
    # Apply runtime configuration
    ${local.proxy_config}
    ${local.gps_config}
  EOF

  fresh_install_init = <<-EOF
    #!/bin/bash
    set -e
    
    # Download and run installer
    cd /tmp
    git clone https://github.com/${var.github_repo}.git cloud-phone
    cd cloud-phone
    
    # Run installer
    chmod +x install-redroid.sh
    ./install-redroid.sh
    
    # Start services
    systemctl start redroid-cloud-phone.target
    
    # Wait for Android to boot
    sleep 30
    
    # Apply runtime configuration
    ${local.proxy_config}
    ${local.gps_config}
  EOF

  proxy_config = var.proxy_enabled ? <<-EOF
    # Configure proxy
    /opt/redroid-scripts/proxy-control.sh enable ${var.proxy_type} ${var.proxy_host} ${var.proxy_port} ${var.proxy_username} ${var.proxy_password}
  EOF : ""

  gps_config = var.gps_enabled ? <<-EOF
    # Configure GPS
    curl -s -X POST http://localhost:8080/location \
      -H "Content-Type: application/json" \
      -d '{"enabled":true,"latitude":${var.gps_latitude},"longitude":${var.gps_longitude}}'
  EOF : ""
}

# Output: Instance details
output "instance_ids" {
  description = "OCIDs of created instances"
  value       = oci_core_instance.cloud_phone[*].id
}

output "instance_public_ips" {
  description = "Public IP addresses"
  value       = oci_core_instance.cloud_phone[*].public_ip
}

output "instance_private_ips" {
  description = "Private IP addresses"
  value       = oci_core_instance.cloud_phone[*].private_ip
}

output "connection_commands" {
  description = "Commands to connect to instances"
  value = [
    for i, instance in oci_core_instance.cloud_phone : {
      name = instance.display_name
      ssh  = "ssh -i ${var.ssh_private_key_path} ubuntu@${instance.public_ip}"
      vnc  = "ssh -i ${var.ssh_private_key_path} -L ${5900 + i}:localhost:5900 ubuntu@${instance.public_ip} -N"
      adb  = "adb connect ${instance.public_ip}:5555"
    }
  ]
}
