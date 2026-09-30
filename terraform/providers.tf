provider "aws" {
  region                      = "us-east-1"
  access_key                  = "test"
  secret_key                  = "test"
  s3_use_path_style           = true
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true

  endpoints {
    s3       = var.localstack_endpoint
    dynamodb = var.localstack_endpoint
    sts      = var.localstack_endpoint
  }
}

# The same broker the platform uses (host listener of infra/docker-compose.yml).
provider "kafka" {
  bootstrap_servers = [var.kafka_bootstrap]
  tls_enabled       = false
  timeout           = 60
}

# Confluent Schema Registry REST API: PUT/GET/DELETE /config/{subject}.
provider "restapi" {
  uri                   = var.schema_registry_url
  write_returns_object  = false
  create_returns_object = false
  headers = {
    "Content-Type" = "application/vnd.schemaregistry.v1+json"
  }
}
