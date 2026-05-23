variable "aws_region" {
  description = "AWS region to deploy resources into."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project name applied as a tag and name prefix to all resources."
  type        = string
  default     = "nimbuskart"
}

variable "environment" {
  description = "Deployment environment (e.g. staging, production)."
  type        = string
  default     = "staging"
}

variable "owner" {
  description = "Team or individual that owns these resources."
  type        = string
  default     = "platform-team"
}

variable "ssh_cidr" {
  description = "CIDR block allowed inbound on port 22. Defaults to 0.0.0.0/0 — restrict in production."
  type        = string
  default     = "0.0.0.0/0"
}

variable "instance_type" {
  description = "EC2 instance type for application servers."
  type        = string
  default     = "t3.micro"
}

variable "stopped_days_threshold" {
  description = "Number of days an EC2 instance may remain stopped before the janitor flags it."
  type        = number
  default     = 14
}

# ── Network ──────────────────────────────────────────────────────────────────

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.20.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets — one per availability zone."
  type        = list(string)
  default     = ["10.20.1.0/24", "10.20.2.0/24"]
}

variable "availability_zones" {
  description = "Availability zones to spread public subnets across."
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]
}
