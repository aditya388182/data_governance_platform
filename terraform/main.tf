# Project 4, Day 6: governance as code, infrastructure included.
#
# One configuration, three workspaces (dev | staging | prod). Every name carries the
# workspace, so the three states own disjoint resources. Production posture: real
# separation is one AWS account per environment with a per-workspace provider
# configuration (assume-role); the workspace mechanics shown here are identical.

locals {
  env = terraform.workspace

  # Registry-as-code: the compatibility mode each dataset DECLARES in
  # contracts/registry.yml (the same file the schema gate enforces) is applied to the
  # runtime Schema Registry here. The runtime authority's configuration is now itself
  # under the merge gate: changing a mode is a PR, the terraform-plan check shows the
  # config change, and only an approved apply performs it.
  registry = yamldecode(file("${path.module}/../contracts/registry.yml"))
  subjects = {
    for name, d in local.registry.datasets :
    "${local.env}.${d.subject}" => d.compatibility
    if try(d.subject, null) != null && try(d.compatibility, null) != null
  }
}

module "lake_bucket" {
  source = "./modules/encrypted_bucket"

  name                = "p4-${local.env}-governed-lake"
  environment         = local.env
  data_classification = var.data_classification
  prevent_destroy     = local.env == "prod"
}

# The transactions topic. Its retention is the Day 5 erasure boundary made explicit:
# segments live this long, DELETE+VACUUM can't reach them, crypto-shredding can.
resource "kafka_topic" "transactions" {
  name               = "${local.env}.payments.transactions"
  partitions         = 3
  replication_factor = 1

  config = {
    "retention.ms" = "604800000" # 7 days
  }
}

# PUT /config/{subject} {"compatibility": MODE}. The registry reads the mode back as
# "compatibilityLevel" (GET) but writes it as "compatibility" (PUT): checked against
# cp-schema-registry 7.6.1's ConfigUpdateRequest/Config classes. Sending both keys (the
# PUT ignores unknown fields) and ignoring the write-only one makes Terraform compare
# compatibilityLevel with what the registry really holds: drift is detected and there is
# no perpetual diff (the F5 trap).
resource "restapi_object" "subject_compatibility" {
  for_each = local.subjects

  path           = "/config/${each.key}"
  create_path    = "/config/${each.key}"
  read_path      = "/config/${each.key}"
  update_path    = "/config/${each.key}"
  destroy_path   = "/config/${each.key}"
  object_id      = each.key
  create_method  = "PUT"
  update_method  = "PUT"
  destroy_method = "DELETE"

  data              = jsonencode({ compatibility = each.value, compatibilityLevel = each.value })
  ignore_changes_to = ["compatibility"]
}
