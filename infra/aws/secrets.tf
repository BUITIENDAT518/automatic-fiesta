resource "random_password" "api_viewer" {
  length  = 40
  special = false
}

resource "random_password" "api_admin" {
  length  = 40
  special = false
}

# 32 random bytes, base64url-encoded = a valid Fernet key.
resource "random_id" "pii_key" { byte_length = 32 }

resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.name}/app"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    DATABASE_URL   = "postgresql://orderhub:${random_password.db.result}@${aws_db_instance.main.address}:5432/orderhub?sslmode=require"
    REDIS_URL      = "rediss://${aws_elasticache_replication_group.main.primary_endpoint_address}:6379/0"
    PII_KEY        = replace(replace(random_id.pii_key.b64_std, "+", "-"), "/", "_")
    API_KEY_VIEWER = random_password.api_viewer.result
    API_KEY_ADMIN  = random_password.api_admin.result
  })
}

# Marketplace app credentials: fill in via the AWS console or CLI (never in git).
resource "aws_secretsmanager_secret" "marketplaces" {
  name                    = "${var.name}/marketplaces"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "marketplaces" {
  secret_id = aws_secretsmanager_secret.marketplaces.id
  secret_string = jsonencode({
    SHOPEE_PARTNER_ID = "", SHOPEE_PARTNER_KEY = "",
    TIKTOK_APP_KEY    = "", TIKTOK_APP_SECRET = "",
    LAZADA_APP_KEY    = "", LAZADA_APP_SECRET = "",
  })
  lifecycle { ignore_changes = [secret_string] }
}
