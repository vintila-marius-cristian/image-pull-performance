from prometheus_client import Counter, Gauge

LABELS = ['job', 'site', 'cluster', 'region', 'path_type', 'artifact']
COMP_LABELS = ['job', 'site', 'cluster', 'region', 'artifact']

# RAW Execution states
probe_runs_total = Counter('artifactory_probe_runs_total', 'Total number of probe executions', LABELS)
probe_failures_total = Counter('artifactory_probe_failures_total', 'Failed absolute probes', LABELS + ['error_class'])
http_status_total = Counter('artifactory_http_status_total', 'HTTP status codes encountered', LABELS + ['status_code'])

# AGGREGATED Run metrics (Gauge used to represent the calculated summary of a RepeatCount loop)
run_min_ttfb_seconds = Gauge('artifactory_run_min_ttfb_seconds', 'Minimum TTFB across repeats', LABELS)
run_max_ttfb_seconds = Gauge('artifactory_run_max_ttfb_seconds', 'Maximum TTFB across repeats', LABELS)
run_avg_ttfb_seconds = Gauge('artifactory_run_avg_ttfb_seconds', 'Average TTFB across repeats', LABELS)
run_p95_ttfb_seconds = Gauge('artifactory_run_p95_ttfb_seconds', 'P95 TTFB across repeats', LABELS)

run_min_duration_seconds = Gauge('artifactory_run_min_duration_seconds', 'Minimum Duration across repeats', LABELS)
run_max_duration_seconds = Gauge('artifactory_run_max_duration_seconds', 'Maximum Duration across repeats', LABELS)
run_avg_duration_seconds = Gauge('artifactory_run_avg_duration_seconds', 'Average Duration across repeats', LABELS)
run_p95_duration_seconds = Gauge('artifactory_run_p95_duration_seconds', 'P95 Duration across repeats', LABELS)

run_avg_speed_bps = Gauge('artifactory_run_average_speed_bytes_per_second', 'Average download speed per cycle', LABELS)
run_success_ratio = Gauge('artifactory_run_success_ratio', 'Ratio of successful repeats', LABELS)
transfer_size_bytes = Gauge('artifactory_transfer_size_bytes', 'Artifact size in bytes', LABELS)

# COMPARISON AGGREGATE metrics
edge_vs_origin_speed_ratio = Gauge('artifactory_edge_vs_origin_speed_ratio', 'Edge avg speed / Origin avg speed', COMP_LABELS)
edge_vs_origin_latency_delta_seconds = Gauge('artifactory_edge_vs_origin_latency_delta_seconds', 'Edge avg TTFB - Origin avg TTFB', COMP_LABELS)
edge_vs_origin_duration_delta_seconds = Gauge('artifactory_edge_vs_origin_duration_delta_seconds', 'Edge avg duration - Origin avg duration', COMP_LABELS)
edge_faster = Gauge('artifactory_edge_faster', '1 if edge avg duration < origin avg duration', COMP_LABELS)
