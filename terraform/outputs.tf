output "workspace" {
  value = local.env

  precondition {
    condition     = contains(["dev", "staging", "prod"], local.env)
    error_message = "Select a workspace first (terraform workspace select dev|staging|prod): the default workspace manages nothing."
  }
}

output "lake_bucket" {
  value = module.lake_bucket.bucket_id
}

output "lake_bucket_tags" {
  value = module.lake_bucket.tags
}

output "kafka_topic" {
  value = kafka_topic.transactions.name
}

output "registry_subjects" {
  value = local.subjects
}
