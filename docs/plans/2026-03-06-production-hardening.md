# Production Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Resolve all 15 identified production readiness gaps across Python code, Dockerfile, and Helm chart.

**Architecture:** Fixes are applied in-place with no architectural changes. Tasks are ordered so failing tests are written first, then implementations, minimising regression risk. Docker and Helm tasks are non-testable via pytest and are verified manually.

**Tech Stack:** Python 3.11, prometheus_client, requests, PyYAML, Docker, Helm 3

---

## Task 1: Add `.dockerignore` and non-root Dockerfile user

**Files:**
- Create: `.dockerignore`
- Modify: `Dockerfile`

**Step 1: Create `.dockerignore`**

```
venv/
.git/
__pycache__/
*.pyc
*.pyo
tests/
docs/
*.md
.env
```

**Step 2: Add non-root user to `Dockerfile`**

Replace the current `Dockerfile` content with:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN adduser --disabled-password --gecos "" appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8080 8081

ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
```

**Step 3: Verify image build excludes venv**

```bash
docker build -t artifactory-edge-exporter:test .
docker image inspect artifactory-edge-exporter:test --format '{{.Size}}'
```

Expected: image size should be well under 200MB (without venv). Compare with `docker history artifactory-edge-exporter:test`.

**Step 4: Verify non-root user**

```bash
docker run --rm artifactory-edge-exporter:test whoami
```

Expected output: `appuser`

---

## Task 2: Fix secret resolution fragility in `config.py`

**Files:**
- Modify: `src/config.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing tests**

Create `tests/test_config.py`:

```python
import os
import unittest
from src.config import JobConfig, load_config


class TestSecretResolution(unittest.TestCase):
    def _make_job(self, **kwargs):
        base = {
            "name": "test",
            "edge_url_base": "http://edge",
            "origin_url_base": "http://origin",
            "artifacts": ["a.bin"],
        }
        base.update(kwargs)
        return JobConfig(base)

    def test_env_prefix_resolves_env_var(self):
        os.environ["TEST_SECRET"] = "mytoken"
        job = self._make_job(password="env:TEST_SECRET")
        self.assertEqual(job.password, "mytoken")
        del os.environ["TEST_SECRET"]

    def test_env_prefix_with_colon_in_var_name_does_not_break(self):
        # Regression: val.split('env:')[1] would fail if env var name contained 'env:'
        # val[4:] is the correct approach
        os.environ["MY_TOKEN"] = "secret"
        job = self._make_job(password="env:MY_TOKEN")
        self.assertEqual(job.password, "secret")
        del os.environ["MY_TOKEN"]

    def test_file_prefix_resolves_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("  filetoken  \n")
            path = f.name
        job = self._make_job(password=f"file:{path}")
        self.assertEqual(job.password, "filetoken")
        os.unlink(path)

    def test_literal_value_returned_as_is(self):
        job = self._make_job(password="plaintext")
        self.assertEqual(job.password, "plaintext")

    def test_none_value_returns_none(self):
        job = self._make_job()
        self.assertIsNone(job.password)


class TestJobConfigValidation(unittest.TestCase):
    def _make_job(self, **kwargs):
        base = {
            "name": "test",
            "edge_url_base": "http://edge",
            "origin_url_base": "http://origin",
            "artifacts": ["a.bin"],
        }
        base.update(kwargs)
        return JobConfig(base)

    def test_missing_edge_url_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self._make_job(edge_url_base=None)
        self.assertIn("edge_url_base", str(ctx.exception))

    def test_missing_origin_url_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self._make_job(origin_url_base=None)
        self.assertIn("origin_url_base", str(ctx.exception))

    def test_empty_artifacts_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self._make_job(artifacts=[])
        self.assertIn("artifacts", str(ctx.exception))

    def test_repeat_count_zero_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self._make_job(repeat_count=0)
        self.assertIn("repeat_count", str(ctx.exception))

    def test_repeat_count_negative_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self._make_job(repeat_count=-1)
        self.assertIn("repeat_count", str(ctx.exception))

    def test_valid_config_does_not_raise(self):
        job = self._make_job()  # should not raise
        self.assertEqual(job.name, "test")


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run to confirm tests fail**

```bash
cd /Users/cristi/custom-blackbox-exporter
source venv/bin/activate
python -m pytest tests/test_config.py -v
```

Expected: Several FAILs — `ValueError` not raised, split fragility not fixed yet.

**Step 3: Update `src/config.py`**

Replace the full file:

```python
import os
import yaml


class JobConfig:
    def __init__(self, data):
        self.name = data.get('name', 'default')
        self.edge_url_base = data.get('edge_url_base')
        self.origin_url_base = data.get('origin_url_base')

        self.artifacts = data.get('artifacts', [])
        if 'artifact_path' in data and not self.artifacts:
            self.artifacts = [data.get('artifact_path')]

        self.auth_method = data.get('auth_method', 'none')
        self.username = self._resolve_secret(data.get('username'))
        self.password = self._resolve_secret(data.get('password'))
        self.timeout = data.get('timeout', 30)
        self.interval = data.get('schedule_interval', 60)

        self.repeat_count = data.get('repeat_count', 1)
        self.warmup_runs = data.get('warmup_runs', 0)
        self.cooldown_seconds = data.get('cooldown_seconds', 0.5)
        self.cache_busting = data.get('cache_busting', True)

        self.tls_verify = data.get('tls_verify', True)
        self.labels = data.get('labels', {})
        self.extra_headers = data.get('extra_headers', {})
        self.max_bytes = data.get('max_bytes', None)

        self._validate()

    def _resolve_secret(self, val):
        if not val:
            return None
        if val.startswith('env:'):
            return os.getenv(val[4:], "")
        if val.startswith('file:'):
            path = val[5:]
            if os.path.exists(path):
                with open(path, 'r') as f:
                    return f.read().strip()
            return ""
        return val

    def _validate(self):
        if not self.edge_url_base:
            raise ValueError(f"Job '{self.name}': edge_url_base is required")
        if not self.origin_url_base:
            raise ValueError(f"Job '{self.name}': origin_url_base is required")
        if not self.artifacts:
            raise ValueError(f"Job '{self.name}': artifacts list must not be empty")
        if self.repeat_count < 1:
            raise ValueError(f"Job '{self.name}': repeat_count must be >= 1, got {self.repeat_count}")


def load_config(path="config.yaml"):
    with open(path, 'r') as f:
        data = yaml.safe_load(f)
    return {
        'port': data.get('server_port', 8080),
        'health_port': data.get('health_port', 8081),
        'jobs': [JobConfig(j) for j in data.get('jobs', [])]
    }
```

**Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_config.py -v
```

Expected: All PASS.

---

## Task 3: Fix `metrics.py` unused import and `probe.py` code quality

**Files:**
- Modify: `src/metrics.py`
- Modify: `src/probe.py`
- Test: `tests/test_probe.py` (extend existing)

**Step 1: Write failing test for in-place sort side-effect**

Add to `tests/test_probe.py` inside `TestProbe`:

```python
def test_calculate_percentile_does_not_mutate_input(self):
    data = [5, 1, 3, 2, 4]
    original = data.copy()
    calculate_percentile(data, 0.95)
    self.assertEqual(data, original, "calculate_percentile must not mutate the input list")
```

**Step 2: Run to confirm it fails**

```bash
python -m pytest tests/test_probe.py::TestProbe::test_calculate_percentile_does_not_mutate_input -v
```

Expected: FAIL — list is mutated.

**Step 3: Fix `src/metrics.py`**

Remove the unused `Summary` import. Change line 1 from:

```python
from prometheus_client import Counter, Gauge, Summary
```

To:

```python
from prometheus_client import Counter, Gauge
```

**Step 4: Fix `src/probe.py` — no in-place sort, fix `transfer_size_bytes`**

In `probe.py`, update `calculate_percentile` and `aggregate_and_record`:

```python
def calculate_percentile(data, percentile):
    if not data:
        return 0
    sorted_data = sorted(data)          # does not mutate caller's list
    k = (len(sorted_data) - 1) * percentile
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_data[int(k)]
    d0 = sorted_data[int(f)] * (c - k)
    d1 = sorted_data[int(c)] * (k - f)
    return d0 + d1
```

In `aggregate_and_record`, replace:

```python
metrics.transfer_size_bytes.labels(*lbls).set(successes[0]['bytes_downloaded'])
```

With:

```python
avg_bytes = sum(r['bytes_downloaded'] for r in successes) / len(successes)
metrics.transfer_size_bytes.labels(*lbls).set(avg_bytes)
```

**Step 5: Run all probe tests**

```bash
python -m pytest tests/test_probe.py -v
```

Expected: All PASS including new mutation test.

---

## Task 4: Fix `main.py` — thread naming, silent health logs, backoff

**Files:**
- Modify: `main.py`
- Test: manual / log inspection

**Step 1: Apply all three fixes to `main.py`**

Replace the full file:

```python
import logging
import time
import threading
import sys
import os
from prometheus_client import start_http_server
from http.server import HTTPServer, BaseHTTPRequestHandler
from src.config import load_config
from src.probe import run_probe

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("exporter.main")

_MAX_BACKOFF = 300  # seconds


def job_loop(job):
    consecutive_failures = 0
    while True:
        try:
            run_probe(job)
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


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/healthz':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress per-request access logs — healthz is hit every 10s by k8s
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
    start_health_server(health_port)
    logger.info(f"Healthz server running on port {health_port}")

    threads = []
    for job in cfg['jobs']:
        t = threading.Thread(
            target=job_loop,
            args=(job,),
            daemon=True,
            name=f"probe-{job.name}"
        )
        t.start()
        threads.append(t)
        logger.info(f"Started thread for job: {job.name} (interval: {job.interval}s)")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
```

**Step 2: Verify thread names appear in logs**

```bash
cd /Users/cristi/custom-blackbox-exporter
source venv/bin/activate
EXPORTER_CONFIG=config.example.yaml python main.py 2>&1 | head -20
```

Expected: Log lines from threads. Health server should not print per-request lines.

**Step 3: Verify backoff by temporarily breaking a URL in config and watching retry interval grow**

No automated test — visual log inspection is sufficient.

---

## Task 5: Add error-path test coverage to `tests/test_client.py`

**Files:**
- Modify: `tests/test_client.py`

**Step 1: Add failing tests**

Append these test methods inside `TestClient`:

```python
@patch('src.client.requests.get')
def test_download_artifact_timeout(self, mock_get):
    import requests as req
    mock_get.side_effect = req.exceptions.Timeout("timed out")

    res = download_artifact("http://edge", "test.bin", self.job, "edge")

    self.assertFalse(res['success'])
    self.assertEqual(res['error_class'], 'timeout')
    self.assertEqual(res['bytes_downloaded'], 0)

@patch('src.client.requests.get')
def test_download_artifact_connection_error(self, mock_get):
    import requests as req
    mock_get.side_effect = req.exceptions.ConnectionError("refused")

    res = download_artifact("http://edge", "test.bin", self.job, "edge")

    self.assertFalse(res['success'])
    self.assertEqual(res['error_class'], 'connection_error')

@patch('src.client.requests.get')
def test_download_artifact_http_error_404(self, mock_get):
    import requests as req
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.raise_for_status.side_effect = req.exceptions.HTTPError(
        response=mock_resp
    )
    mock_get.return_value.__enter__.return_value = mock_resp

    res = download_artifact("http://edge", "test.bin", self.job, "edge")

    self.assertFalse(res['success'])
    self.assertEqual(res['error_class'], 'http_error')
    self.assertEqual(res['status_code'], 404)

@patch('src.client.requests.get')
def test_bearer_auth_header_set(self, mock_get):
    self.job.auth_method = 'bearer'
    self.job.password = 'mytoken'
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_content.return_value = [b"data"]
    mock_get.return_value.__enter__.return_value = mock_resp

    download_artifact("http://edge", "test.bin", self.job, "edge")

    headers = mock_get.call_args[1]['headers']
    self.assertEqual(headers['Authorization'], 'Bearer mytoken')

@patch('src.client.requests.get')
def test_basic_auth_tuple_set(self, mock_get):
    self.job.auth_method = 'basic'
    self.job.username = 'user'
    self.job.password = 'pass'
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_content.return_value = [b"data"]
    mock_get.return_value.__enter__.return_value = mock_resp

    download_artifact("http://edge", "test.bin", self.job, "edge")

    auth = mock_get.call_args[1]['auth']
    self.assertEqual(auth, ('user', 'pass'))

@patch('src.client.requests.get')
def test_max_bytes_sets_range_header(self, mock_get):
    self.job.max_bytes = 1024
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_content.return_value = [b"x" * 100]
    mock_get.return_value.__enter__.return_value = mock_resp

    download_artifact("http://edge", "test.bin", self.job, "edge")

    headers = mock_get.call_args[1]['headers']
    self.assertEqual(headers['Range'], 'bytes=0-1023')
```

**Step 2: Run to confirm new tests fail (or pass for already-working behaviour)**

```bash
python -m pytest tests/test_client.py -v
```

Note: auth and max_bytes tests should already PASS since the logic exists. Timeout/connection/http_error tests should PASS too — they test existing error handling. If any FAIL, investigate before moving on.

**Step 3: Run full test suite to confirm nothing regressed**

```bash
python -m pytest tests/ -v
```

Expected: All PASS.

---

## Task 6: Update `requests` dependency to `2.32.3`

**Files:**
- Modify: `requirements.txt`

**Step 1: Update the pinned version**

Change:

```
requests==2.31.0
```

To:

```
requests==2.32.3
```

**Step 2: Reinstall in venv and run full test suite**

```bash
source venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -v
```

Expected: All PASS. `requests 2.32.3` is API-compatible with 2.31.x.

---

## Task 7: Helm chart security hardening

**Files:**
- Modify: `helm-chart/artifactory-edge-exporter/values.yaml`
- Modify: `helm-chart/artifactory-edge-exporter/templates/deployment.yaml`

**Step 1: Update `values.yaml` — add security defaults and readinessProbe**

Replace `podSecurityContext`, `securityContext`, `livenessProbe`, and add `readinessProbe`:

```yaml
podSecurityContext:
  runAsNonRoot: true
  runAsUser: 1000
  fsGroup: 1000

securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop:
      - ALL

livenessProbe:
  httpGet:
    path: /healthz
    port: health-port
  initialDelaySeconds: 5
  periodSeconds: 10
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /healthz
    port: health-port
  initialDelaySeconds: 5
  periodSeconds: 10
  failureThreshold: 3
```

**Step 2: Update `deployment.yaml` — wire `readinessProbe` unconditionally**

The existing template already has:

```yaml
{{- if .Values.readinessProbe }}
readinessProbe:
  {{- toYaml .Values.readinessProbe | nindent 12 }}
{{- end }}
```

This is correct — since `readinessProbe` is now defined in `values.yaml` by default, it will always render.

**Step 3: Lint and template-render the chart**

```bash
helm lint helm-chart/artifactory-edge-exporter/
helm template test helm-chart/artifactory-edge-exporter/ | grep -A 10 "readinessProbe"
helm template test helm-chart/artifactory-edge-exporter/ | grep -A 5 "securityContext"
```

Expected: `readinessProbe`, `runAsNonRoot: true`, `allowPrivilegeEscalation: false` appear in rendered output.

**Step 4: Verify dev values still render cleanly**

```bash
helm template test helm-chart/artifactory-edge-exporter/ -f helm-chart/artifactory-edge-exporter/values-dev.yaml
```

Expected: No errors.

---

## Final Validation

Run the full test suite one last time:

```bash
source venv/bin/activate
python -m pytest tests/ -v --tb=short
```

Expected: All tests PASS with no warnings.

Lint Helm:

```bash
helm lint helm-chart/artifactory-edge-exporter/
```

Expected: `0 chart(s) failed`.

Build Docker image and verify size and user:

```bash
docker build -t artifactory-edge-exporter:prod .
docker image ls artifactory-edge-exporter:prod
docker run --rm artifactory-edge-exporter:prod whoami
```

Expected: image well under 300MB, user is `appuser`.
