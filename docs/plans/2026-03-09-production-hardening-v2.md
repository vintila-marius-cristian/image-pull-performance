# Production Hardening V2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close remaining production gaps — HTTP session reuse with retry, config validation, graceful shutdown, internal exporter metrics, health/readiness endpoints, and full documentation refresh.

**Architecture:** In-place improvements to the existing sync/threading architecture. No structural changes. `requests.Session` replaces bare `requests.get` for connection pooling. `threading.Event` replaces `while True` for clean shutdown. New `/readyz` endpoint validates probe thread liveness.

**Tech Stack:** Python 3.11, requests 2.32.3, prometheus_client 0.20.0, PyYAML 6.0.1, Docker, Helm 3

---

## Task 1: Extended config validation

**Files:**
- Modify: `src/config.py:55-63`
- Test: `tests/test_config.py`

**Step 1: Write failing tests for invalid timeout, interval, warmup, cooldown**

Add to `tests/test_config.py` inside `TestJobConfigValidation`:

```python
def test_timeout_zero_raises(self):
    with self.assertRaises(ValueError) as ctx:
        self._make_job(timeout=0)
    self.assertIn("timeout", str(ctx.exception))

def test_timeout_negative_raises(self):
    with self.assertRaises(ValueError) as ctx:
        self._make_job(timeout=-5)
    self.assertIn("timeout", str(ctx.exception))

def test_schedule_interval_zero_raises(self):
    with self.assertRaises(ValueError) as ctx:
        self._make_job(schedule_interval=0)
    self.assertIn("schedule_interval", str(ctx.exception))

def test_warmup_runs_negative_raises(self):
    with self.assertRaises(ValueError) as ctx:
        self._make_job(warmup_runs=-1)
    self.assertIn("warmup_runs", str(ctx.exception))

def test_cooldown_seconds_negative_raises(self):
    with self.assertRaises(ValueError) as ctx:
        self._make_job(cooldown_seconds=-0.5)
    self.assertIn("cooldown_seconds", str(ctx.exception))
```

**Step 2: Run tests to confirm they fail**

```bash
cd /Users/cristi/custom-blackbox-exporter && python -m pytest tests/test_config.py -v
```

Expected: 5 new tests FAIL (ValueError not raised).

**Step 3: Add validation to `src/config.py`**

In `_validate()`, after the existing `repeat_count` check, add:

```python
if self.timeout <= 0:
    raise ValueError(f"Job '{self.name}': timeout must be > 0, got {self.timeout}")
if self.interval <= 0:
    raise ValueError(f"Job '{self.name}': schedule_interval must be > 0, got {self.interval}")
if self.warmup_runs < 0:
    raise ValueError(f"Job '{self.name}': warmup_runs must be >= 0, got {self.warmup_runs}")
if self.cooldown_seconds < 0:
    raise ValueError(f"Job '{self.name}': cooldown_seconds must be >= 0, got {self.cooldown_seconds}")
```

**Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_config.py -v
```

Expected: All 16 tests PASS.

**Step 5: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat: validate timeout, interval, warmup, cooldown at startup"
```

---

## Task 2: HTTP session factory with retry and connection pooling

**Files:**
- Modify: `src/client.py`
- Test: `tests/test_client.py`

**Step 1: Write tests for `create_session`**

Add to `tests/test_client.py`:

```python
from src.client import download_artifact, create_session

class TestCreateSession(unittest.TestCase):
    def setUp(self):
        self.job = JobConfig({
            "name": "test",
            "edge_url_base": "http://edge",
            "origin_url_base": "http://orig",
            "artifacts": ["test.bin"],
            "timeout": 5,
        })

    def test_create_session_returns_session(self):
        session = create_session(self.job)
        self.assertIsInstance(session, requests.Session)

    def test_create_session_has_retry_adapter(self):
        session = create_session(self.job)
        adapter = session.get_adapter("https://example.com")
        self.assertEqual(adapter.max_retries.total, 3)
        self.assertIn(502, adapter.max_retries.status_forcelist)
        self.assertIn(503, adapter.max_retries.status_forcelist)
        self.assertIn(504, adapter.max_retries.status_forcelist)

    def test_create_session_sets_tls_verify(self):
        self.job.tls_verify = False
        session = create_session(self.job)
        self.assertFalse(session.verify)
```

**Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_client.py::TestCreateSession -v
```

Expected: FAIL — `create_session` not found.

**Step 3: Implement `create_session` in `src/client.py`**

Add at the top of `src/client.py` after the existing imports:

```python
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
```

Add the factory function before `download_artifact`:

```python
def create_session(job_config):
    """Create a requests.Session with connection pooling and retry for transient errors."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=10,
        pool_maxsize=10,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.verify = job_config.tls_verify
    return session
```

**Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_client.py::TestCreateSession -v
```

Expected: All 3 PASS.

**Step 5: Modify `download_artifact` to accept a session**

Change the function signature from:

```python
def download_artifact(base_url, artifact_path, job_config, path_type):
```

To:

```python
def download_artifact(base_url, artifact_path, job_config, path_type, session=None):
```

Replace the `requests.get(` call with:

```python
        http_get = session.get if session else requests.get
        with http_get(
            url,
            headers=headers,
            auth=auth,
            timeout=job_config.timeout,
            stream=True,
            verify=job_config.tls_verify
        ) as response:
```

**Step 6: Write test for session-based download**

Add to `TestClient`:

```python
@patch('src.client.requests.Session.get')
def test_download_artifact_uses_session(self, mock_session_get):
    session = requests.Session()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_content.return_value = [b"data"]
    mock_session_get.return_value.__enter__.return_value = mock_resp

    res = download_artifact("http://edge", "test.bin", self.job, "edge", session=session)

    self.assertTrue(res['success'])
    mock_session_get.assert_called_once()
```

**Step 7: Run all client tests**

```bash
python -m pytest tests/test_client.py -v
```

Expected: All PASS (existing tests use `session=None` fallback, new test uses session).

**Step 8: Commit**

```bash
git add src/client.py tests/test_client.py
git commit -m "feat: add requests.Session factory with retry and connection pooling"
```

---

## Task 3: Wire session into probe and job loop

**Files:**
- Modify: `src/probe.py:22-39`
- Modify: `main.py:19-34`

**Step 1: Update `execute_cycle` and `run_probe` to accept and pass session**

In `src/probe.py`, change `execute_cycle` signature:

```python
def execute_cycle(base_url, artifact_path, job_config, path_type, session=None):
```

Pass `session` to all `download_artifact` calls in `execute_cycle`:

```python
download_artifact(base_url, artifact_path, job_config, path_type, session=session)
```

and:

```python
res = download_artifact(base_url, artifact_path, job_config, path_type, session=session)
```

Change `run_probe` signature:

```python
def run_probe(job_config, session=None):
```

Pass `session` to `execute_cycle` calls:

```python
edge_results = execute_cycle(job_config.edge_url_base, artifact, job_config, "edge", session=session)
origin_results = execute_cycle(job_config.origin_url_base, artifact, job_config, "origin", session=session)
```

**Step 2: Update `main.py` job_loop to create and use session**

Add import at top of `main.py`:

```python
from src.client import create_session
```

In `job_loop`, create session once before the loop:

```python
def job_loop(job):
    session = create_session(job)
    consecutive_failures = 0
    while True:
        try:
            run_probe(job, session=session)
            consecutive_failures = 0
            time.sleep(job.interval)
        except Exception as e:
            consecutive_failures += 1
            backoff = min(job.interval * (2 ** (consecutive_failures - 1)), _MAX_BACKOFF)
            logger.error(
                f"Error executing probe {job.name} (failure #{consecutive_failures}, "
                f"retrying in {backoff}s): {e}",
                exc_info=True
            )
            time.sleep(backoff)
```

**Step 3: Run all tests to confirm nothing broke**

```bash
python -m pytest tests/ -v
```

Expected: All PASS. Existing tests pass `session=None` by default.

**Step 4: Commit**

```bash
git add src/probe.py main.py
git commit -m "feat: wire session with connection pooling into probe pipeline"
```

---

## Task 4: Internal exporter metrics

**Files:**
- Modify: `src/metrics.py`
- Modify: `src/probe.py`

**Step 1: Add internal metrics to `src/metrics.py`**

Add at the top with the other imports:

```python
from prometheus_client import Counter, Gauge, Info
```

Add at the bottom of `src/metrics.py`:

```python
# Internal exporter metrics
INTERNAL_LABELS = ['job']

exporter_info = Info('artifactory_exporter', 'Artifactory Edge Exporter build information')
exporter_info.info({'version': '1.1.0'})

probe_cycle_duration_seconds = Gauge(
    'artifactory_probe_cycle_duration_seconds',
    'Duration of a complete probe cycle for a job',
    INTERNAL_LABELS
)
probe_last_success_timestamp = Gauge(
    'artifactory_probe_last_success_timestamp',
    'Unix timestamp of the last successful probe cycle',
    INTERNAL_LABELS
)
```

**Step 2: Record metrics in `run_probe`**

In `src/probe.py`, add `import time` (already imported) and update `run_probe`:

At the start of `run_probe`, record the start time:

```python
def run_probe(job_config, session=None):
    cycle_start = time.time()
    logger.info(f"Starting probe benchmark cycle for job: {job_config.name}")
```

At the end of `run_probe` (after the for loop), add:

```python
    cycle_duration = time.time() - cycle_start
    metrics.probe_cycle_duration_seconds.labels(job_config.name).set(cycle_duration)
    metrics.probe_last_success_timestamp.labels(job_config.name).set(time.time())
    logger.info(f"Completed probe cycle for job: {job_config.name} in {cycle_duration:.2f}s")
```

**Step 3: Run all tests**

```bash
python -m pytest tests/ -v
```

Expected: All PASS.

**Step 4: Commit**

```bash
git add src/metrics.py src/probe.py
git commit -m "feat: add internal exporter metrics (cycle duration, last success, build info)"
```

---

## Task 5: Graceful shutdown with SIGTERM handling

**Files:**
- Modify: `main.py`

**Step 1: Rewrite `main.py` with graceful shutdown**

Replace the full `main.py` with:

```python
import logging
import signal
import time
import threading
import os
from prometheus_client import start_http_server
from http.server import HTTPServer, BaseHTTPRequestHandler
from src.config import load_config
from src.probe import run_probe
from src.client import create_session

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("exporter.main")

_MAX_BACKOFF = 300  # seconds

# Global state for health checks
_probe_threads = []
_stop_event = threading.Event()


def job_loop(job, stop_event):
    session = create_session(job)
    consecutive_failures = 0
    while not stop_event.is_set():
        try:
            run_probe(job, session=session)
            consecutive_failures = 0
            stop_event.wait(timeout=job.interval)
        except Exception as e:
            consecutive_failures += 1
            backoff = min(job.interval * (2 ** (consecutive_failures - 1)), _MAX_BACKOFF)
            logger.error(
                f"Error executing probe {job.name} (failure #{consecutive_failures}, "
                f"retrying in {backoff}s): {e}",
                exc_info=True
            )
            stop_event.wait(timeout=backoff)
    session.close()
    logger.info(f"Probe thread for job {job.name} stopped cleanly")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/healthz':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"OK")
        elif self.path == '/readyz':
            alive = [t for t in _probe_threads if t.is_alive()]
            if alive:
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.end_headers()
                self.wfile.write(f"{len(alive)}/{len(_probe_threads)} probe threads alive".encode())
            else:
                self.send_response(503)
                self.send_header('Content-Type', 'text/plain')
                self.end_headers()
                self.wfile.write(b"No probe threads alive")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_health_server(port):
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True, name="health-server")
    t.start()
    return server


def main():
    config_path = os.getenv("EXPORTER_CONFIG", "config.yaml")
    cfg = load_config(config_path)

    start_http_server(cfg['port'])
    logger.info(f"Prometheus metrics exposed on port {cfg['port']}")

    health_port = cfg.get('health_port', 8081)
    health_server = start_health_server(health_port)
    logger.info(f"Health server running on port {health_port} (/healthz, /readyz)")

    global _probe_threads
    for job in cfg['jobs']:
        t = threading.Thread(
            target=job_loop,
            args=(job, _stop_event),
            daemon=True,
            name=f"probe-{job.name}"
        )
        t.start()
        _probe_threads.append(t)
        logger.info(f"Started probe thread: {job.name} (interval: {job.interval}s)")

    def shutdown(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info(f"Received {sig_name}, shutting down gracefully...")
        _stop_event.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    _stop_event.wait()

    # Give probe threads time to finish current cycle
    for t in _probe_threads:
        t.join(timeout=5)

    health_server.shutdown()
    logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
```

**Step 2: Run all tests**

```bash
python -m pytest tests/ -v
```

Expected: All PASS.

**Step 3: Commit**

```bash
git add main.py
git commit -m "feat: graceful shutdown with SIGTERM/SIGINT, readyz endpoint, thread tracking"
```

---

## Task 6: Update Helm readiness probe to use `/readyz`

**Files:**
- Modify: `helm-chart/artifactory-edge-exporter/values.yaml`

**Step 1: Change readinessProbe path**

In `values.yaml`, change `readinessProbe` from:

```yaml
readinessProbe:
  httpGet:
    path: /healthz
    port: health-port
```

To:

```yaml
readinessProbe:
  httpGet:
    path: /readyz
    port: health-port
```

**Step 2: Lint the chart**

```bash
helm lint helm-chart/artifactory-edge-exporter/
```

Expected: 0 failures.

**Step 3: Verify rendered template**

```bash
helm template test helm-chart/artifactory-edge-exporter/ | grep -A 5 "readinessProbe"
```

Expected: Shows `/readyz` path.

**Step 4: Commit**

```bash
git add helm-chart/artifactory-edge-exporter/values.yaml
git commit -m "feat: use /readyz endpoint for Kubernetes readiness probe"
```

---

## Task 7: Comprehensive README rewrite

**Files:**
- Modify: `README.md`

**Step 1: Replace README.md with complete documentation**

```markdown
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
| `artifactory_run_success_ratio` | Gauge | Ratio of successful runs (0.0–1.0) |
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
```

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: comprehensive README with full feature, metric, and config reference"
```

---

## Task 8: Update HANDOVER.md

**Files:**
- Modify: `docs/HANDOVER.md`

**Step 1: Update HANDOVER.md to reflect V2 changes**

Update the following sections:

1. **Current Objective**: Change from COMPLETED (2026-03-06) to reflect V2 completion
2. **What Was Implemented (Latest)**: Add V2 changes
3. **Architecture Summary**: Update to mention sessions, graceful shutdown, readyz
4. **Technical Debt**: Remove items that were fixed, update remaining items
5. **Testing Status**: Update test count
6. **Production Readiness Status**: Update

The full updated content will be provided during execution — it combines the existing content with the V2 changes.

**Step 2: Commit**

```bash
git add docs/HANDOVER.md
git commit -m "docs: update HANDOVER.md with production hardening V2 changes"
```

---

## Task 9: Final validation

**Step 1: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short
```

Expected: All tests PASS.

**Step 2: Lint Helm chart**

```bash
helm lint helm-chart/artifactory-edge-exporter/
```

Expected: 0 failures.

**Step 3: Verify Docker build**

```bash
docker build -t artifactory-edge-exporter:test .
```

Expected: Builds successfully.

**Step 4: Verify rendered Helm template shows /readyz**

```bash
helm template test helm-chart/artifactory-edge-exporter/ | grep -A 5 "readinessProbe"
```

Expected: Shows `path: /readyz`.
