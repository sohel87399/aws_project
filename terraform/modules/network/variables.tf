variable "project" {
  description = "Project name applied as a tag and name prefix to all resources."
  type        = string
}

variable "environment" {
  description = "Deployment environment (e.g. staging, production)."
  type        = string
}

variable "owner" {
  description = "Team or individual that owns these resources."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
}

variable "public_subnet_cidrs" {
  description = "List of CIDR blocks for public subnets — one entry per availability zone."
  type        = list(string)
}

variable "availability_zones" {
  description = "List of availability zones to spread public subnets across."
  type        = list(string)
}

variable "ssh_cidr" {
  description = "CIDR allowed to reach port 22. Defaults to 0.0.0.0/0 (restrict in production)."
  type        = string
}
