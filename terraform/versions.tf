terraform {
  # Pinned: 1.9.x keeps the classic S3 + DynamoDB locking without deprecation noise
  # (1.11+ deprecates dynamodb_table in favour of S3-native use_lockfile).
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.100"
    }
    kafka = {
      source  = "Mongey/kafka"
      version = "~> 0.13"
    }
    restapi = {
      source  = "Mastercard/restapi"
      version = "~> 3.0"
    }
  }
}
