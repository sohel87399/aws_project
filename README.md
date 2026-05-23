# DevOps Engineer Assignment

## Overview

This repository contains the solution for the DevOps Engineer assignment. It includes:

- **Part A**: Terraform infrastructure code targeting LocalStack, including a reusable `network` module.
- **Part B**: A Python-based "Cost Janitor" script that identifies and optionally deletes untagged or stale AWS resources, with a GitHub Actions workflow for automated execution.
- **Part C**: Architecture design documentation.

## How to run locally

### Prerequisites

- [Terraform](https://developer.hashicorp.com/terraform/downloads) >= 1.5
- [LocalStack](https://docs.localstack.cloud/getting-started/installation/) running locally (`localstack start`)
- Python 3.11+
- `pip install -r janitor/requirements.txt`

### Terraform (LocalStack)

```bash
cd terraform
terraform init
terraform validate
terraform fmt -check
terraform apply
```

### Janitor script

```bash
# Dry-run mode (no deletions, produces report.json)
python janitor/janitor.py --dry-run

# Delete mode (respects Protected=true tag)
python janitor/janitor.py --delete

# Target a specific region
python janitor/janitor.py --dry-run --region us-east-1
```

### Run tests

```bash
cd janitor
pytest tests/ -v
```

### GitHub Actions

Push a branch and open a PR — the `cost-janitor` workflow triggers automatically.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    GitHub Actions                    │
│  cost-janitor.yml  ──►  janitor.py  ──►  report.json │
└──────────────────────────────┬──────────────────────┘
                               │ boto3 / LocalStack
               ┌───────────────▼──────────────────┐
               │         AWS (or LocalStack)        │
               │                                    │
               │  VPC 10.20.0.0/16                  │
               │  ├── public-subnet-1 (us-east-1a)  │
               │  │     └── EC2 app-1 (t3.micro)    │
               │  ├── public-subnet-2 (us-east-1b)  │
               │  │     └── EC2 app-2 (t3.micro)    │
               │  ├── Internet Gateway               │
               │  └── Security Group (80/443/22)     │
               │                                    │
               │  S3  nimbuskart-staging-app-logs    │
               │      (versioned, TLS-only policy)   │
               │                                    │
               │  EBS orphan-vol (20 GB gp3,         │
               │      intentionally unattached)      │
               └────────────────────────────────────┘

Terraform
  └── modules/network  (VPC, subnets, IGW, route table, SG)
  └── root module      (EC2 × 2, S3 bucket, EBS orphan)
```

The janitor script queries AWS resource APIs, evaluates each resource against tagging and age policies defined in `constants.py`, and emits a structured JSON report. In `--delete` mode it skips any resource carrying the `Protected=true` tag.

## Decisions & deviations

- **SSH CIDR `0.0.0.0/0`:** followed spec but flagged as unsafe — `var.ssh_cidr` exists so real deployments restrict it to a known bastion or VPN CIDR.
- **No S3 bucket policy in spec:** added a deny-non-TLS bucket policy proactively — omitting it violates AWS security best practice by allowing plain-HTTP access to the log bucket.
- **AMI ID placeholder:** used `ami-00000000` since LocalStack does not validate AMI IDs — a real deployment needs a `data "aws_ami"` source lookup to resolve a current, region-specific AMI.
- **Stopped-instance age uses launch time as fallback:** `StateTransitionReason` is unreliable on LocalStack; the janitor falls back to `LaunchTime` when no parseable timestamp is present — this gap is documented in Known Limitations.
- **EIP cost model:** used the `$0.005/hr` idle rate from the AWS public pricing page, cited inline in `constants.py`.

## Trade-offs

- Dry-run mode is the default to prevent accidental deletions in shared environments; `--delete` requires an explicit flag and still skips any resource tagged `Protected=true`.
- **Given one more week:** multi-account support would be added by iterating over accounts via `organizations:ListAccounts` and assuming a cross-account role with `sts:AssumeRole` in each target account, so a single pipeline run covers the entire AWS Organization.
- **Given one more week:** hard-coded pricing constants in `constants.py` would be replaced with live lookups against the AWS Price List API or Cost Explorer, eliminating drift when AWS adjusts rates.
- **Given one more week:** each run would append its `report.json` to a versioned S3 prefix (`s3://nimbuskart-finops/janitor/YYYY/MM/DD/report.json`) so the FinOps team can query historical trend data with Athena instead of each run overwriting the last report.

## AI usage disclosure

_Describe which parts of this submission were produced with AI assistance, which tools were used, and how outputs were reviewed and validated._
