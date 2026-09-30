# terraform/ — governance as code, infrastructure included

One configuration, three workspaces. State lives in LocalStack S3 (`tf-state`) with a DynamoDB lock
table (`tf-locks`); each workspace has its own state object, `env:/<workspace>/governance/terraform.tfstate`.

| What | Where | Why |
|---|---|---|
| Encrypted, classified bucket | `modules/encrypted_bucket` | SSE, versioning, public access blocked; `Environment` and `DataClassification` validated at plan time |
| `<ws>.payments.transactions` topic | `main.tf` (Mongey/kafka) | retention is the Day 5 erasure boundary, made explicit |
| Registry compatibility per subject | `main.tf` (Mastercard/restapi) | modes come from `contracts/registry.yml`: the runtime authority's config is under the merge gate |

## Once per machine

```bash
python scripts/tf_localstack.py bootstrap   # state bucket (versioned) + lock table; idempotent
cd terraform && terraform init              # commit the generated .terraform.lock.hcl
terraform workspace new dev && terraform workspace new staging && terraform workspace new prod
```

## Every change

```bash
terraform workspace select dev      # never plan in `default`: it is refused by design
terraform plan                      # the same plan the terraform-plan check posts on your PR
```

Apply happens from `main` only: `terraform-apply` runs after merge and waits for approval of the
`production` environment. Locally you apply the dev stack the same way the job does.

## Guarantees and how they are enforced

- **Locking.** Every plan and apply takes the DynamoDB lock. A second writer gets
  `Error acquiring the state lock` with the holder's ID, operation and user; `python
  scripts/tf_localstack.py state` shows who holds it. `terraform force-unlock <ID>` only when the
  holder is certainly dead.
- **Tags are mandatory.** An empty or unknown `Environment`/`DataClassification` fails the module's
  `validation` blocks: nothing is created. `terraform test` in `modules/encrypted_bucket` proves it
  offline with a mocked provider.
- **Prod cannot be destroyed by accident.** `lifecycle.prevent_destroy` must be a literal, so the
  module declares the bucket twice and `count` picks the protected one in prod. `terraform plan
  -destroy` in prod fails with "Instance cannot be destroyed".
- **The default workspace manages nothing.** The workspace output has a precondition, and the tag
  validation rejects `default`. That is also why the CI gate runs `validate` inside each workspace.
- **No perpetual diff on the registry.** The registry reads the mode back as `compatibilityLevel` but
  writes it as `compatibility`. The resource sends both keys and ignores the write-only one, so a
  plan compares what the registry really holds.

## Workspaces here vs production

Workspaces here share one LocalStack, one broker and one registry, so every name carries the
workspace. In production the separation is **one AWS account per environment**: the S3 backend
lives in a dedicated state account, each workspace's provider assumes that environment's role
(OIDC from CI, never static keys), and prod state is readable only by the prod pipeline. The
workspace mechanics are identical; only the credentials and the blast radius change.

## Drift

`scripts/drift_check.py` (nightly DAG `drift_detection`) runs `terraform plan -detailed-exitcode` per
workspace: 0 = clean, 2 = differs, 1 = could not plan (failing closed, never "clean"). An attribute
changed outside Terraform raises `TerraformDrift` naming it. Remediate by re-applying the code, or
by adopting the change in code through a PR. Never by editing state. See
[docs/runbooks/terraform_drift.md](../docs/runbooks/terraform_drift.md).

Newer Terraform (1.11+) deprecates `dynamodb_table` in favour of S3-native `use_lockfile`; this repo
pins 1.9.x to keep the classic lock table. Migrating is a one-line backend change plus `terraform
init -reconfigure`.
