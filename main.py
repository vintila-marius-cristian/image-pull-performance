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
