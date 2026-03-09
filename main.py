import logging
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
