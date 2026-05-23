output "vpc_id" {
  description = "ID of the provisioned VPC."
  value       = module.network.vpc_id
}

output "subnet_ids" {
  description = "IDs of the public subnets."
  value       = module.network.public_subnet_ids
}

output "bucket_name" {
  description = "Name of the S3 application-log bucket."
  value       = aws_s3_bucket.app_logs.id
}

output "orphan_volume_id" {
  description = "ID of the intentionally unattached EBS volume (orphan for Part B)."
  value       = aws_ebs_volume.orphan.id
}

output "instance_ids" {
  description = "IDs of the two EC2 application instances."
  value       = aws_instance.app[*].id
}

output "app_security_group_id" {
  description = "ID of the application security group."
  value       = module.network.app_security_group_id
}
