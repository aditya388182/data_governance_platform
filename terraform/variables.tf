variable "localstack_endpoint" {
  description = "LocalStack edge endpoint (the AWS stand-in)."
  type        = string
  default     = "http://localhost:4566"
}

variable "kafka_bootstrap" {
  description = "Kafka bootstrap server for topic management."
  type        = string
  default     = "localhost:29092"
}

variable "schema_registry_url" {
  description = "Schema Registry base URL (the runtime authority whose config Terraform manages)."
  type        = string
  default     = "http://localhost:8081"
}

variable "data_classification" {
  description = "DataClassification tag of the governed lake bucket."
  type        = string
  default     = "confidential"
}
