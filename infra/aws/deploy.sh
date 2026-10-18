#!/usr/bin/env bash
# Usage: infra/aws/deploy.sh   (needs AWS credentials, docker, terraform)
set -euo pipefail
cd "$(dirname "$0")"
TAG="${TAG:-$(git rev-parse --short HEAD)}"
REGION="${AWS_REGION:-ap-southeast-1}"

terraform init -input=false
terraform apply -input=false -auto-approve -target=aws_ecr_repository.app
REPO="$(terraform output -raw ecr_repository_url)"

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${REPO%%/*}"
docker build --platform linux/amd64 -t "$REPO:$TAG" ../..
docker push "$REPO:$TAG"

terraform apply -input=false -var "image_tag=$TAG" "$@"
terraform output dashboard_url
