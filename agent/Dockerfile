# Build the Linux agent binary as a static, dependency-free artifact.
FROM golang:1.22-alpine AS build
WORKDIR /src
COPY go.mod ./
RUN go mod download || true
COPY . .
ARG VERSION=docker
RUN CGO_ENABLED=0 go build -ldflags "-s -w -X main.Version=${VERSION}" \
    -o /out/netpulse-agent ./cmd/netpulse-agent/

# Minimal runtime image (the binary is static; scratch is sufficient).
FROM scratch
COPY --from=build /out/netpulse-agent /netpulse-agent
ENTRYPOINT ["/netpulse-agent"]
