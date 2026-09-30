# Remote state on LocalStack S3 + a DynamoDB lock table (Project 4, Day 6).
#
# Created once by `python scripts/tf_localstack.py bootstrap` (the state bucket, with
# versioning, and the tf-locks table keyed by LockID). Workspaces get their own state
# object: env:/<workspace>/governance/terraform.tfstate.
#
# Every plan/apply takes the lock first. Two engineers (or CI and a human) never
# write the same state at once: the second one gets "Error acquiring the state lock"
# with the holder's ID, and Block 6.1 produces exactly that on purpose.
#
# The credentials are LocalStack's dummy pair. Production: a real bucket in a
# dedicated state account, credentials from OIDC, never in code.
terraform {
  backend "s3" {
    bucket         = "tf-state"
    key            = "governance/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "tf-locks"
    endpoints = {
      s3       = "http://localhost:4566"
      dynamodb = "http://localhost:4566"
    }
    use_path_style              = true
    skip_credentials_validation = true
    skip_requesting_account_id  = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    access_key                  = "test"
    secret_key                  = "test"
  }
}
