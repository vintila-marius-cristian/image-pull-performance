# Artifactory Edge Exporter

A production-ready Prometheus-compatible exporter that rigorously benchmarks **JFrog Artifactory edge-node download performance** against direct/origin access. 

## Benchmark Model
The system uses **Mode 1: Artifact download benchmark**. It relies on direct HTTP `GET` streams to map precise delivery times (TTFB and total Transfer Durations).

### Cache Avoidance Strategy
To deliver "Real Performance metrics" and avoid contaminating data with local container buffer caching or Node-level storage behavior:
1. We **do not rely on `docker pull` or `containerd` daemons**. Calling container runtimes inevitably contaminates data with deeply nested filesystem caches. 
2. We inject `Cache-Control: no-cache, no-store, must-revalidate` explicitly via REST.
3. We append a uniquely generated UUID parameters `?nocache=<uuid>` to bust any standard unauthenticated proxy-layer CDNs lying in front of the Artifactory ecosystem natively.
4. Payload chunks are strictly iterators. They touch RAM and discard instantly without polluting local ephemeral disk or `tmpfs`.

## Requirements
- Kubernetes 1.20+
- Helm 3.0+

## Local Execution
Ensure you have Python 3.11+:
```bash
pip install -r requirements.txt
export EXPORTER_CONFIG=config.example.yaml
python main.py
```

### Docker Compose
You can also run the exporter locally using Docker Compose:
```bash
docker-compose up -d
```

## Running the Unit Tests
```bash
python -m unittest discover -s tests
```

## Metric Semantics
- `artifactory_run_avg_ttfb_seconds`: Pure network latency from TCP request instantiation until the very first stream chunk arrives successfully. (Combines DNS + Handshake + Proxies into one).
- `artifactory_run_success_ratio`: Given `N` repeat loops on a target artifact, what percentage natively executed an HTTP `2xx`.
- `artifactory_edge_vs_origin_speed_ratio`: Divides the raw averaged mathematically computed Bytes Per second transferred by the Edge context natively versus the Origin context.

## Helm Deployment
We strongly recommend leveraging the standard Helm deployment included:

```bash
helm upgrade --install artifactory-edge-exporter ./helm-chart/artifactory-edge-exporter -f my-values.yaml
```

**Note**: Read `docs/HANDOVER.md` for continuation parameters and developer handoff guidelines.
