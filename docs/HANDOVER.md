# Artifactory Edge Exporter - Handover

## Agent Continuation Rules
- **ALWAYS read `HANDOVER.md` first** before starting any work.
- **ALWAYS update `HANDOVER.md`** at the end of your iteration.
- **NEVER make silent architectural changes** without documenting them here.
- **ALWAYS document incomplete work and blocked items.**
- **ALWAYS record config or schema changes** in the config section.

---

## Current Objective
**COMPLETED** — Production hardening V2 (2026-03-09). All remaining production readiness gaps resolved. See `docs/plans/2026-03-09-production-hardening-v2.md` for the full plan.

## What Was Implemented (Latest — Production Hardening V2)
- **HTTP session pooling:** `create_session()` factory in `client.py` creates a `requests.Session` with `HTTPAdapter` (pool_connections=10, pool_maxsize=10). TCP/TLS connections reused per job thread.
- **Automatic retry:** `urllib3.util.retry.Retry` configured for 502/503/504 with 3 retries and 0.5s exponential backoff. Applied via `HTTPAdapter.max_retries`.
- **Extended config validation:** `timeout > 0`, `schedule_interval > 0`, `warmup_runs >= 0`, `cooldown_seconds >= 0` now validated at startup with clear `ValueError` messages.
- **Graceful shutdown:** `threading.Event` replaces `while True`. SIGTERM/SIGINT handlers set the stop event. `job_loop` uses `stop_event.wait(timeout=...)` instead of `time.sleep()`. Threads joined with 5s timeout on shutdown. Sessions closed cleanly.
- **Readiness endpoint:** New `/readyz` endpoint checks probe thread liveness. Returns 200 with thread count if alive, 503 if all dead. Helm `readinessProbe` updated to use `/readyz`.
- **Internal exporter metrics:** Added `artifactory_exporter_info` (Info, version=1.1.0), `artifactory_probe_cycle_duration_seconds` (Gauge per job), `artifactory_probe_last_success_timestamp` (Gauge per job).
- **Documentation:** Comprehensive README rewrite with full feature list, all 21 metrics documented, configuration reference tables, endpoint reference, architecture overview.
- **Tests:** 5 new config validation tests, 4 new client tests (session factory, session usage). Total: 31 tests, all passing.

## What Was Implemented (Production Hardening V1 — 2026-03-06)
- **Docker:** Created `.dockerignore` (excludes `venv/`, `helm-chart/`, `k8s/`, etc.). Dockerfile now runs as non-root `appuser` (UID 1000) using `COPY --chown`. `PYTHONDONTWRITEBYTECODE=1` moved before `pip install`.
- **Config hardening:** `_resolve_secret` fixed (`val[4:]` / `val[5:]` instead of fragile `.split()`). `_validate()` added — raises `ValueError` on missing URLs, empty artifacts, `repeat_count < 1`. `load_config` raises if no jobs defined. Missing env/file secrets now emit `logger.warning` instead of silently returning `""`.
- **Code quality:** Unused `Summary` import removed from `metrics.py`. `calculate_percentile` uses `sorted()` (no in-place mutation). `transfer_size_bytes` now averages all successful runs instead of using `successes[0]`.
- **main.py:** Threads named (`probe-{job.name}`, `health-server`). `HealthHandler.log_message` suppressed. Exponential backoff added to `job_loop`.
- **Dependencies:** `requests` bumped from `2.31.0` to `2.32.3` (CVE fix for auth header leak on redirects).
- **Helm:** `podSecurityContext`, `securityContext`, `readinessProbe` added as defaults. Helm template syntax bug in `configmap.yaml` fixed.

## What Was Implemented (Initial — Artifact Download Benchmark)
- **Mode 1: Artifact download benchmark** with explicit cache-busting logic.
- `config.py` supports `artifacts` list, `warmup_runs`, `repeat_count`, `cooldown_seconds`.
- `probe.py` aggregates statistics (min, max, avg, P95) across repeat runs.
- Advanced aggregation and comparison metrics in `metrics.py`.
- Unit tests leveraging `unittest.mock`.
- Helm chart with ServiceMonitor, ConfigMap, Secret support.

## Architecture Summary
- `main.py`: Entry point daemon. Schedules one thread per job. Handles SIGTERM/SIGINT for graceful shutdown. Serves `/healthz` (liveness) and `/readyz` (readiness) on port 8081. Exposes Prometheus metrics on port 8080.
- `config.py`: Parses YAML config with secret resolution (`env:`, `file:`, literal). Validates all fields at startup.
- `client.py`: `requests.Session`-based streaming HTTP client with connection pooling and urllib3 retry. Measures TTFB and throughput per download.
- `probe.py`: Orchestrates warmup + measurement runs. Calculates percentiles. Records per-artifact and comparison metrics. Tracks cycle duration and last success timestamp.
- `metrics.py`: All Prometheus metric definitions — 14 per-artifact, 4 comparison, 3 internal.

## Configuration Model
All fields with validation:
- `edge_url_base` (string, **required**): Base URL for edge Artifactory.
- `origin_url_base` (string, **required**): Base URL for origin Artifactory.
- `artifacts` (list[string], **required**): Artifact paths to benchmark.
- `auth_method` (string, default `"none"`): `"bearer"`, `"basic"`, or `"none"`.
- `username` / `password` (string): Supports `env:VAR`, `file:/path`, or literal.
- `timeout` (int, default 30, must be > 0): HTTP request timeout in seconds.
- `schedule_interval` (int, default 60, must be > 0): Seconds between probe cycles.
- `repeat_count` (int, default 1, must be >= 1): Measurement runs per cycle.
- `warmup_runs` (int, default 0, must be >= 0): Discarded warmup runs.
- `cooldown_seconds` (float, default 0.5, must be >= 0): Sleep between runs.
- `cache_busting` (bool, default true): Cache-busting headers and query params.
- `tls_verify` (bool, default true): TLS certificate verification.
- `max_bytes` (int, optional): Limit download via HTTP Range header.
- `labels` (dict): Custom `site`, `cluster`, `region` labels.
- `extra_headers` (dict): Additional HTTP headers.

## Deployment Model
- Standard Kubernetes `Deployment` using Helm.
- Exporter config via ConfigMap volume mount to `/app/config/config.yaml`.
- Secrets via Pod environment variables or file mounts.
- Helm `ServiceMonitor` for Prometheus Operator integration.
- Liveness: `/healthz` (always 200). Readiness: `/readyz` (checks thread liveness).

## Known Issues
- `requests` lacks native DNS/TCP/TLS handshake duration metrics. TTFB is the closest available benchmark.
- Concurrency bounded by Python GIL. AsyncIO (`httpx`) rewrite recommended for hundreds of jobs.

## Assumptions Made
- HA Artifactory reverse proxies do NOT strip `?nocache` query parameters.
- Standard Prometheus Operator scraping intervals (15s/30s) are sufficient.

## Open Questions
- Should we migrate to `pycurl` for deeper DNS/TCP-layer metrics?
- Is AQL random artifact discovery critical for Phase 3?

## Technical Debt
- No integrated Artifactory API structural verification. Assumes HTTP `2xx` = success.
- `_resolve_secret` returns `""` for unresolved secrets (with warning). Operator must ensure env vars are set.
- `readOnlyRootFilesystem: true` in Helm assumes no filesystem writes. `/tmp` not mounted as `emptyDir`.
- Test `test_env_prefix_with_colon_in_var_name_does_not_break` name is misleading — doesn't test colon-containing env var names.
- `startupProbe` not defined in Helm — consider adding if probe initialization exceeds 5s.

## Next Recommended Steps
1. Implement AQL automated discovery for random target generation.
2. Refactor to `asyncio` and `httpx` for scaling.
3. Add integration test suite with mocked Nginx proxy.
4. Validate HTTP chunk delivery jitter.

## Testing Status
- 31 unit tests covering: config validation (16 tests), client success/error paths + session factory (12 tests), probe orchestration + percentile math (3 tests).
- All tests passing.
- Requires live integration test suite for end-to-end validation.

## Production Readiness Status
- **Ready for production rollout.** Non-root container, full config validation at startup, secret resolution with warnings, HTTP session pooling with retry, exponential backoff, graceful SIGTERM shutdown, `/healthz` + `/readyz` endpoints, Helm security contexts, internal exporter metrics, and comprehensive unit test coverage.

## Instructions for Next Agent
- Review `values.yaml` to understand how `config.py` imports secret targets.
- Do not modify `.fullname` macros in Helm without verifying test suites.
- Run `python -m pytest tests/ -v` before any changes.

## Validation Checklist
```bash
# Local tests
python -m pytest tests/ -v

# Validate Helm
helm lint helm-chart/artifactory-edge-exporter/
helm template test helm-chart/artifactory-edge-exporter/ -f helm-chart/artifactory-edge-exporter/values-dev.yaml

# Run Locally (Python)
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export EXPORTER_CONFIG=config.example.yaml
python main.py

# Run Locally (Docker Compose)
docker-compose up -d

# Verify endpoints
curl http://localhost:8081/healthz   # expect 200 OK
curl http://localhost:8081/readyz    # expect 200 with thread count
curl http://localhost:8080/metrics   # expect Prometheus metrics
```
