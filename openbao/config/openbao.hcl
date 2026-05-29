ui = true

listener "tcp" {
  address         = "0.0.0.0:8210"
  cluster_address = "0.0.0.0:8211"
  tls_disable     = true
}

storage "file" {
  path = "/openbao/data"
}

api_addr     = "http://openbao:8210"
cluster_addr = "http://openbao:8211"
