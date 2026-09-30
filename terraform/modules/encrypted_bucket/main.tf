# encrypted_bucket: a bucket that cannot exist unencrypted, public, or unclassified.

locals {
  tags = merge(var.extra_tags, {
    Environment        = var.environment
    DataClassification = var.data_classification
    ManagedBy          = "terraform"
  })

  bucket_id  = one(concat(aws_s3_bucket.this[*].id, aws_s3_bucket.protected[*].id))
  bucket_arn = one(concat(aws_s3_bucket.this[*].arn, aws_s3_bucket.protected[*].arn))
}

# lifecycle.prevent_destroy must be a literal: Terraform reads lifecycle blocks before
# any variable exists. So the module declares the bucket twice and count picks one:
# `protected` (prod) carries prevent_destroy = true, `this` (dev, staging) does not.
resource "aws_s3_bucket" "this" {
  count = var.prevent_destroy ? 0 : 1

  bucket        = var.name
  force_destroy = true
  tags          = local.tags
}

resource "aws_s3_bucket" "protected" {
  count = var.prevent_destroy ? 1 : 0

  bucket = var.name
  tags   = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

# SSE-S3 here (LocalStack). Production: sse_algorithm = "aws:kms" with a customer-managed
# key, the same KMS that would hold Day 4's KEK.
resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = local.bucket_id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "this" {
  bucket = local.bucket_id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket = local.bucket_id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
