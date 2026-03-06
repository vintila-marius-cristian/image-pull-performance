# Artifactory Edge Exporter Helm Chart

This repository contains the official Helm chart to deploy the JFrog Artifactory Edge Exporter. This exporter compares the latency and throughput of downloading an artifact through an Artifactory Edge node relative to an Artifactory Origin node.

## Prerequisites

- Kubernetes 1.20+
- Helm 3.0+

## Installing the Chart

To install the chart with the release name `my-exporter`:

```bash
helm install my-exporter ./helm-chart/artifactory-edge-exporter -f ./helm-chart/artifactory-edge-exporter/values-prod.yaml -n monitoring --create-namespace
```

## Uninstalling the Chart

To uninstall/delete the `my-exporter` deployment:

```bash
helm uninstall my-exporter -n monitoring
```

## Secret & Configuration Management

**Never hardcode secrets** in the `exporterConfig`.

This chart supports mapping either inline secrets or existing secrets into the Pods. 
The recommended robust pattern is mapping secret values to Environment variables (`extraEnv` or `env`) and referencing those variables inside the `exporterConfig` using the `env:` prefix syntax parsed by `config.py`.

Example `values.yaml` snippet mapping a secret dynamically without leaving text in the GitOps deployment manifests:
```yaml
env:
  - name: JFROG_TOKEN
    valueFrom:
      secretKeyRef:
        name: existing-k8s-secret-ref
        key: my-token

exporterConfig:
  jobs:
    - name: secure-job
      auth_method: bearer
      password: "env:JFROG_TOKEN"
```

### Exposing Metrics

Configure `serviceMonitor.enabled: true` to dynamically spin up a `ServiceMonitor` CustomResource, instructing the local `Prometheus Operator` to scrape the `metrics` port (default 8080) at the specified `interval`.
