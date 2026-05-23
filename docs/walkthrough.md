# Walkthrough

This document provides a step-by-step guide to running the full solution locally.

## 1. Prerequisites

| Tool | Version | Install |
|---|---|---|
| Terraform | >= 1.5 | https://developer.hashicorp.com/terraform/downloads |
| LocalStack | >= 3.x | `pip install localstack` |
| Python | >= 3.11 | https://python.org |
| AWS CLI | >= 2.x | https://aws.amazon.com/cli/ |

## 2. Start LocalStack

```bash
localstack start
```

Verify it is running:

```bash
curl http://localhost:4566/_localstack/health
```

## 3. Apply Terraform

```bash
cd terraform
terraform init
terraform validate
terraform fmt -check
terraform apply -auto-approve
```

Expected output includes the VPC ID, subnet IDs, and Internet Gateway ID.

## 4. Install Python dependencies

```bash
pip install -r janitor/requirements.txt
```

## 5. Run the janitor in dry-run mode

```bash
python janitor/janitor.py --dry-run --region us-east-1
```

This produces `report.json` in the current directory. No resources are deleted.

## 6. Inspect the report

```bash
cat report.json
```

The report lists every flagged resource, the reasons it was flagged, and whether it is protected.

## 7. Run in delete mode (optional)

```bash
python janitor/janitor.py --delete --region us-east-1
```

Resources tagged `Protected=true` are skipped. All others that are flagged will be deleted.

## 8. Run unit tests

```bash
cd janitor
pytest tests/ -v
```

## 9. GitHub Actions

Push a branch and open a pull request against `main`. The `cost-janitor` workflow will:

1. Lint the Python code with flake8.
2. Run the unit test suite.
3. Start LocalStack and execute a dry-run scan.
4. Upload `report.json` as a workflow artifact.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Connection refused` on port 4566 | LocalStack not running | `localstack start` |
| `NoCredentialsError` | boto3 can't find credentials | Set `AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test` |
| `terraform init` fails | Missing provider cache | Delete `.terraform/` and retry |
| Tests fail with `ImportError` | Wrong working directory | Run `pytest` from the `janitor/` directory |
