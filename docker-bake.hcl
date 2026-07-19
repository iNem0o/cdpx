variable "VERSION" {
  default = "0.1.0"
}

variable "REVISION" {
  default = "uncommitted"
}

# Local cache export requires a containerd-backed builder; CI runners use the
# plain docker driver, so they opt out with DEV_CACHE=0.
variable "DEV_CACHE" {
  default = "1"
}

group "default" {
  targets = ["dev", "runtime"]
}

target "_common" {
  context    = "."
  dockerfile = "Dockerfile"
}

target "dev" {
  inherits = ["_common"]
  target   = "dev"
  tags     = ["cdpx-dev:local"]
  cache-from = DEV_CACHE == "1" ? ["type=local,src=.cache/buildkit-dev"] : []
  cache-to   = DEV_CACHE == "1" ? ["type=local,dest=.cache/buildkit-dev,mode=max"] : []
}

target "ci" {
  inherits = ["_common"]
  target   = "ci"
  tags     = ["cdpx-ci:local"]
}

target "runtime" {
  inherits = ["_common"]
  target   = "runtime"
  tags     = ["cdpx-runtime:dev"]
  args = {
    VERSION  = VERSION
    REVISION = REVISION
  }
}

target "embedded" {
  inherits = ["_common"]
  target   = "embedded"
}
