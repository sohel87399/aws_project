terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  # LocalStack — credentials are ignored but must be non-empty
  access_key = "test"
  secret_key = "test"

  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true

  endpoints {
    ec2 = "http://localhost:4566"
    s3  = "http://localhost:4566"
    iam = "http://localhost:4566"
  }
}

locals {
  common_tags = {
    Project     = var.project
    Environment = var.environment
    Owner       = var.owner
    ManagedBy   = "terraform"
  }
}

# ── Network module ────────────────────────────────────────────────────────────

module "network" {
  source = "./modules/network"

  project             = var.project
  environment         = var.environment
  owner               = var.owner
  vpc_cidr            = var.vpc_cidr
  public_subnet_cidrs = var.public_subnet_cidrs
  availability_zones  = var.availability_zones
  ssh_cidr            = var.ssh_cidr
}

# ── EC2 instances ─────────────────────────────────────────────────────────────
# ami-00000000 is a LocalStack placeholder — replace with a real AMI for AWS.

resource "aws_instance" "app" {
  count = 2

  ami                    = "ami-00000000"
  instance_type          = var.instance_type
  subnet_id              = module.network.public_subnet_ids[count.index]
  vpc_security_group_ids = [module.network.app_security_group_id]

  tags = merge(local.common_tags, {
    Name = "${var.project}-${var.environment}-app-${count.index + 1}"
  })
}

# ── S3 application-log bucket ─────────────────────────────────────────────────

resource "aws_s3_bucket" "app_logs" {
  bucket = "${var.project}-${var.environment}-app-logs"

  tags = merge(local.common_tags, {
    Name = "${var.project}-${var.environment}-app-logs"
  })
}

resource "aws_s3_bucket_versioning" "app_logs" {
  bucket = aws_s3_bucket.app_logs.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "app_logs" {
  bucket = aws_s3_bucket.app_logs.id

  # Expire non-current object versions after 30 days to control storage cost.
  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

resource "aws_s3_bucket_public_access_block" "app_logs" {
  bucket = aws_s3_bucket.app_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Proactive deviation: the spec did not include a bucket policy, but allowing
# plain-HTTP access to a log bucket is a security gap. This policy denies any
# request that does not use TLS. Documented in README "Decisions & deviations".
resource "aws_s3_bucket_policy" "app_logs_tls_only" {
  bucket = aws_s3_bucket.app_logs.id

  # The public-access block must be in place before a policy can be applied.
  depends_on = [aws_s3_bucket_public_access_block.app_logs]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyNonTLS"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          "arn:aws:s3:::${aws_s3_bucket.app_logs.id}",
          "arn:aws:s3:::${aws_s3_bucket.app_logs.id}/*",
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      },
    ]
  })
}

# ── Orphan EBS volume (intentional — used by Part B janitor) ──────────────────
# This volume is deliberately left unattached so the Cost Janitor can detect
# and report it as an orphaned resource.

resource "aws_ebs_volume" "orphan" {
  availability_zone = var.availability_zones[0]
  size              = 20
  type              = "gp3"

  tags = merge(local.common_tags, {
    Name = "${var.project}-${var.environment}-orphan-vol"
  })
}
