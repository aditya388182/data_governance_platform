variable "name" {
  description = "Bucket name (S3 naming rules)."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.name))
    error_message = "Bucket name must be 3-63 characters of a-z, 0-9, '.', '-'."
  }
}

# The two mandatory tags. Each is validated, so an untagged or unclassified bucket is a
# PLAN-TIME error: nothing is created, nothing needs cleaning up. This is the IaC twin of
# the PII gate's default-deny: an unclassified field cannot merge, an unclassified bucket
# cannot plan.
variable "environment" {
  description = "Environment tag: dev | staging | prod."
  type        = string
  nullable    = false

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment tag is mandatory (dev|staging|prod): an untagged bucket is a plan-time error."
  }
}

variable "data_classification" {
  description = "DataClassification tag: public | internal | confidential | restricted."
  type        = string
  nullable    = false

  validation {
    condition     = contains(["public", "internal", "confidential", "restricted"], var.data_classification)
    error_message = "DataClassification tag is mandatory (public|internal|confidential|restricted): an unclassified bucket is a plan-time error, like an unclassified field at the PII gate."
  }
}

variable "prevent_destroy" {
  description = "true in prod: Terraform refuses any plan that would destroy the bucket."
  type        = bool
  default     = false
}

variable "extra_tags" {
  description = "Additional tags (cannot override the mandatory two)."
  type        = map(string)
  default     = {}
}
