import logging
import os
import yaml

logger = logging.getLogger("exporter.config")


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
            env_name = val[4:]
            result = os.getenv(env_name)
            if result is None:
                logger.warning(f"Environment variable '{env_name}' is not set — credential will be empty")
                return ""
            return result
        if val.startswith('file:'):
            path = val[5:]
            if os.path.exists(path):
                with open(path, 'r') as f:
                    return f.read().strip()
            logger.warning(f"Secret file '{path}' does not exist — credential will be empty")
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
    jobs = [JobConfig(j) for j in data.get('jobs', [])]
    if not jobs:
        raise ValueError("Configuration must define at least one job")
    return {
        'port': data.get('server_port', 8080),
        'health_port': data.get('health_port', 8081),
        'jobs': jobs
    }
