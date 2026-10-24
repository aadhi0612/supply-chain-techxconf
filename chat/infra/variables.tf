variable "region" {
  type    = string
  default = "us-west-2"
}
variable "name" {
  type    = string
  default = "siliconflow-chat"
}
variable "model_id" {
  type        = string
  default     = "us.anthropic.claude-haiku-5-5"
  description = "Bedrock inference profile id"
}
variable "allowed_origins" {
  type    = list(string)
  default = ["https://main.d2skuf17sh79ex.amplifyapp.com", "http://localhost:8777"]
}
variable "reserved_concurrency" {
  type        = number
  default     = 3
  description = "Hard cap on parallel invocations (-1 removes the cap)"
}
variable "per_ip_per_min" {
  type    = number
  default = 8
}
variable "daily_cap" {
  type    = number
  default = 1500
}
variable "api_rate" {
  type    = number
  default = 2
}
variable "api_burst" {
  type    = number
  default = 5
}
