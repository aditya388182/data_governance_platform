# terraform test (offline: the AWS provider is mocked). Proves the module's contract:
# mandatory tags are enforced at PLAN time, and prod buckets are the protected variant.

mock_provider "aws" {}

variables {
  name                = "p4-test-governed-lake"
  environment         = "dev"
  data_classification = "confidential"
}

run "tagged_bucket_plans" {
  command = plan

  assert {
    condition     = aws_s3_bucket.this[0].tags["DataClassification"] == "confidential" && aws_s3_bucket.this[0].tags["Environment"] == "dev"
    error_message = "both mandatory tags must be on the bucket"
  }

  assert {
    condition     = length(aws_s3_bucket.protected) == 0
    error_message = "a dev bucket must not be the protected variant"
  }
}

run "unclassified_bucket_is_a_plan_time_error" {
  command = plan

  variables {
    data_classification = ""
  }

  expect_failures = [var.data_classification]
}

run "untagged_environment_is_a_plan_time_error" {
  command = plan

  variables {
    environment = ""
  }

  expect_failures = [var.environment]
}

run "prod_bucket_is_protected" {
  command = plan

  variables {
    environment     = "prod"
    prevent_destroy = true
  }

  assert {
    condition     = length(aws_s3_bucket.protected) == 1 && length(aws_s3_bucket.this) == 0
    error_message = "prod must use the prevent_destroy variant"
  }
}
