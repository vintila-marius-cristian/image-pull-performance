import time
import uuid
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import logging

logger = logging.getLogger("exporter.client")

def create_session(job_config):
    """Create a requests.Session with connection pooling and retry for transient errors."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=10,
        pool_maxsize=10,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.verify = job_config.tls_verify
    return session


def download_artifact(base_url, artifact_path, job_config, path_type, session=None):
    """
    Downloads an artifact rigorously. 
    Applies aggressive Cache-Busting explicitly to measure True delivery latency, not local Nginx buffering.
    """
    headers = job_config.extra_headers.copy()
    auth = None
    
    if job_config.auth_method == 'bearer' and job_config.password:
        headers['Authorization'] = f"Bearer {job_config.password}"
    elif job_config.auth_method == 'basic' and job_config.username and job_config.password:
        auth = (job_config.username, job_config.password)
    
    if job_config.max_bytes:
        headers['Range'] = f"bytes=0-{job_config.max_bytes - 1}"

    url = f"{base_url.rstrip('/')}/{artifact_path.lstrip('/')}"
    
    if job_config.cache_busting:
        headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        headers['Pragma'] = 'no-cache'
        nocache_str = f"nocache={uuid.uuid4().hex}"
        url = f"{url}&{nocache_str}" if "?" in url else f"{url}?{nocache_str}"

    start_time = time.time()
    ttfb = None
    bytes_downloaded = 0
    status_code = 0
    error_class = None

    try:
        http_get = session.get if session else requests.get
        with http_get(
            url,
            headers=headers,
            auth=auth,
            timeout=job_config.timeout,
            stream=True,
            verify=job_config.tls_verify
        ) as response:
            status_code = response.status_code
            response.raise_for_status()
            
            for chunk in response.iter_content(chunk_size=8192):
                if ttfb is None:
                    ttfb = time.time() - start_time
                if chunk:
                    bytes_downloaded += len(chunk)
                    
            if ttfb is None:
                ttfb = time.time() - start_time

    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response else 500
        error_class = "http_error"
        logger.error(f"HTTP error for {path_type} ({url}): {e}")
    except requests.exceptions.ConnectionError as e:
        error_class = "connection_error"
        logger.error(f"Connection error for {path_type} ({url}): {e}")
    except requests.exceptions.Timeout as e:
        error_class = "timeout"
        logger.error(f"Timeout for {path_type} ({url}): {e}")
    except Exception as e:
        error_class = "unknown_error"
        logger.error(f"Unknown error for {path_type} ({url}): {e}")

    end_time = time.time()
    duration = end_time - start_time

    if ttfb is None:
        ttfb = duration

    throughput = (bytes_downloaded / duration) if duration > 0 else 0

    return {
        "status_code": status_code,
        "bytes_downloaded": bytes_downloaded,
        "ttfb": ttfb,
        "duration": duration,
        "throughput": throughput,
        "success": error_class is None and 200 <= status_code < 300,
        "error_class": error_class
    }
