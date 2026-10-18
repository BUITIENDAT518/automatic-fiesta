output "dashboard_url" { value = "http://${aws_lb.main.dns_name}" }
output "ecr_repository_url" { value = aws_ecr_repository.app.repository_url }
output "app_secret_arn" { value = aws_secretsmanager_secret.app.arn }
output "marketplaces_secret_arn" { value = aws_secretsmanager_secret.marketplaces.arn }
