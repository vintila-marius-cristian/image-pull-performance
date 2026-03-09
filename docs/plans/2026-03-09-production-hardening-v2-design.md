# Production Hardening V2 — Design

**Date:** 2026-03-09
**Status:** Approved
**Goal:** Close remaining production readiness gaps: HTTP session reuse with retry, config validation, graceful shutdown, internal exporter metrics, health endpoint liveness, and documentation refresh.

---

## 1. Session-Based HTTP Client with Retry

**File:** `src/client.py`

Add `create_session(job_config)` factory:
- Returns a `requests.Session` with `urllib3.util.retry.Retry` configured for transient errors (502, 503, 504), 3 retries, exponential backoff factor 0.5.
- `HTTPAdapter` with `pool_connections=10`, `pool_maxsize=10`.
- Session reused per-job thread for connection pooling (DNS/TLS amortized).

Modify `download_artifact` to accept optional `session` parameter:
- Uses `session.get(...)` when provided, `requests.get(...)` as fallback.
- No other behavioral changes — same TTFB/throughput measurement, same error handling.

## 2. Extended Config Validation

**File:** `src/config.py`

Add validation in `_validate()`:
- `timeout > 0` (must be positive)
- `schedule_interval > 0` (must be positive)
- `warmup_runs >= 0` (non-negative)
- `cooldown_seconds >= 0` (non-negative)

Fail-fast at startup with clear `ValueError` messages.

## 3. Internal Exporter Metrics

**File:** `src/metrics.py`

Add:
- `exporter_info` — `Info` metric with `version` label (read from a `VERSION` constant)
- `probe_cycle_duration_seconds` — `Gauge` with `[job]` label. Set after each `run_probe` cycle completes.
- `probe_last_success_timestamp` — `Gauge` with `[job]` label. Set to `time.time()` after successful cycle.

**File:** `src/probe.py`

Record `probe_cycle_duration_seconds` and `probe_last_success_timestamp` at the end of `run_probe`.

## 4. Graceful Shutdown + Health Improvements

**File:** `main.py`

- Replace `while True` with `threading.Event` stop signal.
- Register `SIGTERM` and `SIGINT` handlers that set the stop event.
- `job_loop` checks `stop_event.is_set()` instead of `while True`, uses `stop_event.wait(timeout=interval)` instead of `time.sleep(interval)`.
- Track probe threads in a list accessible to `HealthHandler`.
- `HealthHandler.do_GET('/healthz')` returns 503 if all probe threads are dead. Returns 200 with thread status summary otherwise.
- Add `/readyz` endpoint that checks at least one probe thread is alive.

## 5. Tests

- `test_config.py`: 4 new tests for invalid timeout, interval, warmup_runs, cooldown_seconds.
- `test_client.py`: Test `create_session` returns configured session with retry adapter.
- `test_main.py`: Test health endpoint returns 503 when threads dead (new file).

## 6. Documentation

- **README.md**: Full rewrite reflecting all current capabilities, metrics list, endpoints, configuration reference, deployment options.
- **HANDOVER.md**: Update with V2 changes, current state, remaining tech debt.

## Non-Goals

- No async migration (current threading model is appropriate for this workload).
- No new Helm chart changes (security contexts already hardened in V1).
- No breaking config changes.
