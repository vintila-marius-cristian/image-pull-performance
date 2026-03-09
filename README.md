# Artifactory Edge Exporter

A production-ready Prometheus exporter that benchmarks **JFrog Artifactory edge-node download performance** against origin/direct access. Measures TTFB, download duration, throughput, and success ratio per artifact across configurable repeat cycles.

## How It Works

The exporter runs as a long-lived daemon with one thread per configured job. Each job periodically:

1. Downloads test artifacts from both the **edge** and **origin** Artifactory URLs
2. Measures **TTFB** (Time to First Byte), **total download duration**, and **throughput** (bytes/sec)
3. Repeats each download N times (configurable), discarding warmup runs
4. Aggregates results: min, max, average, P95 percentiles
5. Computes edge-vs-origin comparison metrics (speed ratio, latency delta)
6. Exposes all metrics on a Prometheus `/metrics` endpoint

### Cache Avoidance

To ensure measurements reflect real delivery performance (not cached data):

- `Cache-Control: no-cache, no-store, must-revalidate` headers injected on every request
- Unique `?nocache=<uuid>` query parameter appended to bust CDN/proxy caches
- Payload streamed in memory-only chunks (no disk writes)

## Features

- **Multi-job support** — run independent benchmarks for different sites/artifacts concurrently
- **Connection pooling** — `requests.Session` with keep-alive reuses TCP/TLS connections per job
- **Automatic retry** — transient HTTP errors (502, 503, 504) retried 3x with exponential backoff
- **Exponential backoff** — job-level retry with backoff up to 300s on repeated failures
- **Graceful shutdown** — handles SIGTERM/SIGINT for clean Kubernetes pod termination
- **Health endpoints** — `/healthz` (liveness) and `/readyz` (readiness, checks probe thread liveness)
- **Flexible auth** — bearer token, basic auth, or no auth per job
- **Secret resolution** — credentials from environment variables (`env:VAR`), files (`file:/path`), or literals
- **Cache busting** — configurable per job, enabled by default
- **TLS verification** — configurable per job
- **Range downloads** — optional `max_bytes` to limit download size via HTTP Range header
- **Custom labels** — attach `site`, `cluster`, `region` labels to all metrics
- **Custom headers** — inject arbitrary HTTP headers per job

## Requirements

- Python 3.11+
- Kubernetes 1.20+ (for Helm deployment)
- Helm 3.0+ (for Helm deployment)

## Quick Start

### Local (Python)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export EXPORTER_CONFIG=config.example.yaml
python main.py
```

### Docker Compose

```bash
docker-compose up -d
```

Metrics: `http://localhost:8080/metrics`
Health: `http://localhost:8081/healthz`
Readiness: `http://localhost:8081/readyz`

### Helm (Kubernetes)

```bash
helm upgrade --install artifactory-edge-exporter \
  ./helm-chart/artifactory-edge-exporter \
  -f my-values.yaml
```

See `helm-chart/README.md` for full Helm documentation.

## Configuration

Configuration is a YAML file, path set via `EXPORTER_CONFIG` env var (default: `config.yaml`).

### Global Settings

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `server_port` | int | `8080` | Port for Prometheus metrics endpoint |
| `health_port` | int | `8081` | Port for health/readiness endpoints |
| `jobs` | list | required | List of benchmark job configurations |

### Job Settings

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | `"default"` | Job identifier (used in metric labels and thread names) |
| `edge_url_base` | string | **required** | Base URL for edge Artifactory instance |
| `origin_url_base` | string | **required** | Base URL for origin Artifactory instance |
| `artifacts` | list[string] | **required** | Artifact paths to benchmark (relative to base URL) |
| `auth_method` | string | `"none"` | Authentication method: `"bearer"`, `"basic"`, or `"none"` |
| `username` | string | `null` | Username for basic auth (supports secret resolution) |
| `password` | string | `null` | Password/token (supports secret resolution) |
| `timeout` | int | `30` | HTTP request timeout in seconds (must be > 0) |
| `schedule_interval` | int | `60` | Seconds between probe cycles (must be > 0) |
| `repeat_count` | int | `1` | Number of measurement runs per cycle (must be >= 1) |
| `warmup_runs` | int | `0` | Discarded warmup runs before measurement (must be >= 0) |
| `cooldown_seconds` | float | `0.5` | Sleep between individual runs (must be >= 0) |
| `cache_busting` | bool | `true` | Enable cache-busting headers and query params |
| `tls_verify` | bool | `true` | Verify TLS certificates |
| `max_bytes` | int | `null` | Limit download to N bytes via HTTP Range header |
| `labels` | dict | `{}` | Custom labels: `site`, `cluster`, `region` |
| `extra_headers` | dict | `{}` | Additional HTTP headers sent with every request |

### Secret Resolution

Credential fields (`username`, `password`) support three formats:

| Format | Example | Behavior |
|--------|---------|----------|
| `env:VAR_NAME` | `env:JFROG_TOKEN` | Read from environment variable |
| `file:/path` | `file:/run/secrets/token` | Read from file (whitespace stripped) |
| literal | `my-token-value` | Used as-is (not recommended for production) |

### Example Configuration

```yaml
server_port: 8080
health_port: 8081
jobs:
  - name: "eu_benchmark"
    edge_url_base: "https://edge-eu.jfrog.io/artifactory"
    origin_url_base: "https://origin.jfrog.io/artifactory"
    artifacts:
      - "generic-local/benchmark/test-100m.bin"
      - "generic-local/benchmark/test-250m.bin"
    auth_method: "bearer"
    password: "env:JFROG_TOKEN"
    timeout: 120
    schedule_interval: 300
    repeat_count: 5
    warmup_runs: 1
    cooldown_seconds: 1.0
    cache_busting: true
    tls_verify: true
    labels:
      site: "europe-central"
      cluster: "k8s-eu-1"
      region: "eu-central-1"
```

## HTTP Endpoints

| Endpoint | Port | Description |
|----------|------|-------------|
| `/metrics` | 8080 | Prometheus scrape target (all metrics) |
| `/healthz` | 8081 | Liveness probe — always returns 200 OK |
| `/readyz` | 8081 | Readiness probe — returns 200 if probe threads alive, 503 if all dead |

## Exported Metrics

### Per-Artifact Metrics

Labels: `job`, `site`, `cluster`, `region`, `path_type` (edge/origin), `artifact`

| Metric | Type | Description |
|--------|------|-------------|
| `artifactory_probe_runs_total` | Counter | Total probe executions |
| `artifactory_probe_failures_total` | Counter | Failed probes (extra label: `error_class`) |
| `artifactory_http_status_total` | Counter | HTTP status codes encountered (extra label: `status_code`) |
| `artifactory_run_success_ratio` | Gauge | Ratio of successful runs (0.0-1.0) |
| `artifactory_run_min_ttfb_seconds` | Gauge | Minimum TTFB across repeat runs |
| `artifactory_run_max_ttfb_seconds` | Gauge | Maximum TTFB across repeat runs |
| `artifactory_run_avg_ttfb_seconds` | Gauge | Average TTFB across repeat runs |
| `artifactory_run_p95_ttfb_seconds` | Gauge | P95 TTFB across repeat runs |
| `artifactory_run_min_duration_seconds` | Gauge | Minimum total download duration |
| `artifactory_run_max_duration_seconds` | Gauge | Maximum total download duration |
| `artifactory_run_avg_duration_seconds` | Gauge | Average total download duration |
| `artifactory_run_p95_duration_seconds` | Gauge | P95 total download duration |
| `artifactory_run_average_speed_bytes_per_second` | Gauge | Average download throughput |
| `artifactory_transfer_size_bytes` | Gauge | Average artifact size downloaded |

### Edge vs Origin Comparison Metrics

Labels: `job`, `site`, `cluster`, `region`, `artifact`

| Metric | Type | Description |
|--------|------|-------------|
| `artifactory_edge_vs_origin_speed_ratio` | Gauge | Edge speed / Origin speed (>1 = edge faster) |
| `artifactory_edge_vs_origin_latency_delta_seconds` | Gauge | Edge TTFB - Origin TTFB (<0 = edge faster) |
| `artifactory_edge_vs_origin_duration_delta_seconds` | Gauge | Edge duration - Origin duration (<0 = edge faster) |
| `artifactory_edge_faster` | Gauge | 1 if edge avg duration < origin, else 0 |

### Internal Exporter Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `artifactory_exporter_info` | Info | `version` | Exporter build information |
| `artifactory_probe_cycle_duration_seconds` | Gauge | `job` | Duration of last complete probe cycle |
| `artifactory_probe_last_success_timestamp` | Gauge | `job` | Unix timestamp of last successful probe |

## Alerting Rules

Example Prometheus alerting rules are provided in `alerts.yaml`:

- **ArtifactoryEdgeSlowerThanOrigin** — fires when edge is consistently slower than origin for 15 minutes
- **ArtifactoryProbeFailing** — fires on sustained probe failures over 5 minutes
- **ArtifactoryEdgeHighLatency** — fires when edge TTFB exceeds 1 second for 10 minutes

## Architecture

```
main.py                  Entry point, thread orchestration, health server, signal handling
src/
  config.py              YAML config parsing, secret resolution, validation
  client.py              HTTP client with session pooling, retry, TTFB measurement
  probe.py               Probe orchestration, statistical aggregation, metric recording
  metrics.py             Prometheus metric definitions
tests/
  test_config.py         Config validation and secret resolution tests
  test_client.py         HTTP client success/error path tests
  test_probe.py          Probe orchestration and percentile math tests
helm-chart/              Kubernetes Helm chart with security hardening
alerts.yaml              Prometheus alerting rules
```

## Running Tests

```bash
python -m pytest tests/ -v
```

## Docker

```bash
docker build -t artifactory-edge-exporter .
docker run -p 8080:8080 -p 8081:8081 \
  -v $(pwd)/config.example.yaml:/app/config/config.yaml:ro \
  -e EXPORTER_CONFIG=/app/config/config.yaml \
  artifactory-edge-exporter
```

The container runs as non-root user `appuser` (UID 1000).
