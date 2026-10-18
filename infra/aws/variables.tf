variable "name" {
  type    = string
  default = "orderhub"
}

variable "region" {
  type    = string
  default = "ap-southeast-1"
}

variable "image_tag" {
  description = "Tag of the image in ECR to deploy"
  type        = string
  default     = "latest"
}

variable "replicas" {
  type    = object({ api = number, collector = number, processor = number, tracker = number })
  default = { api = 2, collector = 2, processor = 2, tracker = 1 }
}

variable "seed_mock_shops" {
  description = "Seed demo shops with simulated orders (turn off once real shops are added)"
  type        = bool
  default     = true
}

variable "certificate_arn" {
  description = "Optional ACM certificate ARN; enables HTTPS on the load balancer"
  type        = string
  default     = ""
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}
