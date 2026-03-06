import logging
import time
import math
from .client import download_artifact
from . import metrics

logger = logging.getLogger("exporter.probe")

def calculate_percentile(data, percentile):
    if not data:
        return 0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * percentile
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_data[int(k)]
    d0 = sorted_data[int(f)] * (c - k)
    d1 = sorted_data[int(c)] * (k - f)
    return d0 + d1

def execute_cycle(base_url, artifact_path, job_config, path_type):
    # Warmup runs (ignored metrics)
    for w in range(job_config.warmup_runs):
        logger.debug(f"[{job_config.name}] Warmup {w+1}/{job_config.warmup_runs} for {path_type}")
        download_artifact(base_url, artifact_path, job_config, path_type)
        if job_config.cooldown_seconds:
            time.sleep(job_config.cooldown_seconds)
            
    # Measure runs
    results = []
    for r in range(job_config.repeat_count):
        logger.debug(f"[{job_config.name}] Run {r+1}/{job_config.repeat_count} for {path_type}")
        res = download_artifact(base_url, artifact_path, job_config, path_type)
        results.append(res)
        if r < job_config.repeat_count - 1 and job_config.cooldown_seconds:
            time.sleep(job_config.cooldown_seconds)
            
    return results

def aggregate_and_record(job_name, site, cluster, region, path_type, artifact, results):
    lbls = [job_name, site, cluster, region, path_type, artifact]
    
    successes = [r for r in results if r['success']]
    success_ratio = len(successes) / len(results) if results else 0
    metrics.run_success_ratio.labels(*lbls).set(success_ratio)
    
    for r in results:
        metrics.probe_runs_total.labels(*lbls).inc()
        if not r['success']:
            metrics.probe_failures_total.labels(*(lbls + [r['error_class'] or "unknown"])).inc()
        metrics.http_status_total.labels(*(lbls + [str(r['status_code'])])).inc()

    if not successes:
        return None  # Return none if absolute failure to avoid math zero divisions

    # Aggregate mathematics
    ttfbs = [r['ttfb'] for r in successes]
    durations = [r['duration'] for r in successes]
    throughputs = [r['throughput'] for r in successes]

    avg_ttfb = sum(ttfbs) / len(successes)
    avg_dur = sum(durations) / len(successes)
    avg_speed = sum(throughputs) / len(successes)

    metrics.run_min_ttfb_seconds.labels(*lbls).set(min(ttfbs))
    metrics.run_max_ttfb_seconds.labels(*lbls).set(max(ttfbs))
    metrics.run_avg_ttfb_seconds.labels(*lbls).set(avg_ttfb)
    metrics.run_p95_ttfb_seconds.labels(*lbls).set(calculate_percentile(ttfbs, 0.95))

    metrics.run_min_duration_seconds.labels(*lbls).set(min(durations))
    metrics.run_max_duration_seconds.labels(*lbls).set(max(durations))
    metrics.run_avg_duration_seconds.labels(*lbls).set(avg_dur)
    metrics.run_p95_duration_seconds.labels(*lbls).set(calculate_percentile(durations, 0.95))

    metrics.run_avg_speed_bps.labels(*lbls).set(avg_speed)
    avg_bytes = sum(r['bytes_downloaded'] for r in successes) / len(successes)
    metrics.transfer_size_bytes.labels(*lbls).set(avg_bytes)
    
    return {
        "avg_ttfb": avg_ttfb,
        "avg_duration": avg_dur,
        "avg_speed": avg_speed,
    }

def run_probe(job_config):
    logger.info(f"Starting probe benchmark cycle for job: {job_config.name}")
    labels = job_config.labels
    site = labels.get('site', 'unknown')
    cluster = labels.get('cluster', 'unknown')
    region = labels.get('region', 'unknown')

    for artifact in job_config.artifacts:
        logger.info(f"Targeting artifact ({job_config.repeat_count} runs): {artifact}")
        
        edge_results = execute_cycle(job_config.edge_url_base, artifact, job_config, "edge")
        origin_results = execute_cycle(job_config.origin_url_base, artifact, job_config, "origin")

        edge_agg = aggregate_and_record(job_config.name, site, cluster, region, "edge", artifact, edge_results)
        origin_agg = aggregate_and_record(job_config.name, site, cluster, region, "origin", artifact, origin_results)

        if edge_agg and origin_agg:
            comp_lbls = [job_config.name, site, cluster, region, artifact]
            
            if origin_agg['avg_speed'] > 0:
                metrics.edge_vs_origin_speed_ratio.labels(*comp_lbls).set(edge_agg['avg_speed'] / origin_agg['avg_speed'])
            
            metrics.edge_vs_origin_latency_delta_seconds.labels(*comp_lbls).set(edge_agg['avg_ttfb'] - origin_agg['avg_ttfb'])
            metrics.edge_vs_origin_duration_delta_seconds.labels(*comp_lbls).set(edge_agg['avg_duration'] - origin_agg['avg_duration'])
            metrics.edge_faster.labels(*comp_lbls).set(1 if edge_agg['avg_duration'] < origin_agg['avg_duration'] else 0)
