import time

import requests

from osmfinder._constants import OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS, USER_AGENT

_RETRYABLE_EXCEPTIONS = (
    requests.ConnectionError,
    requests.Timeout,
    requests.TooManyRedirects,
)


def get_with_retries(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    retries: int = 10,
    retry_delay: float = 1.0,
) -> requests.Response:
    """
    GET *url* with automatic retries on transient network errors.

    Args:
        url: Target URL.
        headers: Optional request headers.
        timeout: Request timeout in seconds.
        retries: Maximum number of attempts (default 10).
        retry_delay: Base delay in seconds between retries. Actual delay is
            doubled after each failed attempt (exponential back-off).

    Returns:
        requests.Response: The successful response.

    Raises:
        requests.RequestException: The last raised exception after all retries
            are exhausted.
    """
    last_exception: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return requests.get(url, headers=headers, timeout=timeout)
        except _RETRYABLE_EXCEPTIONS as exc:
            last_exception = exc
            if attempt < retries:
                time.sleep(retry_delay * (2 ** (attempt - 1)))
    raise last_exception  # type: ignore[misc]


def get_file_size(url: str, retries: int = 3) -> float:
    """
    Get a size of the file in bytes without downloading it.

    Args:
        url: URL of the file.
        retries: Maximum number of attempts in case of temporary errors (default 3).

    Returns:
        float: Size of the file, NaN if it can't be determined.
    """
    for attempt in range(retries):
        time.sleep(2**attempt - 1)
        try:
            response = requests.head(
                url,
                # a compressed response wouldn't have the size of the file
                headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
                timeout=OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS,
                allow_redirects=True,
            )
        except _RETRYABLE_EXCEPTIONS:
            continue
        if response.status_code == 429 or response.status_code >= 500:
            continue

        size = response.headers.get("Content-Length", "")
        return float(size) if response.ok and size.isdigit() else float("nan")

    return float("nan")
