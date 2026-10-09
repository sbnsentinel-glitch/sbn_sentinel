import httpx
import asyncio
import logging
import random
import time
import email.utils
from datetime import datetime, timezone
from typing import Dict, Any, AsyncGenerator, Optional
from app.connectors.base_connector import ConnectorException

logger = logging.getLogger(__name__)

MAX_INDIVIDUAL_DELAY = 60.0
MAX_TOTAL_RETRY_BUDGET = 120.0


def parse_retry_after(header_val: Optional[str], default: float = 1.0) -> float:
    """
    Parses Retry-After header supporting both delta-seconds (integer/float)
    and HTTP-date formats (RFC 7231 / RFC 9110).
    """
    if not header_val:
        return default
    header_str = str(header_val).strip()
    try:
        # Check if integer or float delta-seconds
        val = float(header_str)
        return max(0.0, val)
    except ValueError:
        pass

    try:
        # Check if RFC 1123 / HTTP-date
        parsed_tuple = email.utils.parsedate_to_datetime(header_str)
        if parsed_tuple is not None:
            now = datetime.now(timezone.utc)
            if parsed_tuple.tzinfo is None:
                parsed_tuple = parsed_tuple.replace(tzinfo=timezone.utc)
            delta = (parsed_tuple - now).total_seconds()
            return max(0.0, delta)
    except Exception:
        pass

    return default


class RateLimitedException(ConnectorException):
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"Rate limited. Retry after {retry_after:.2f}s", failure_code="RATE_LIMITED")


class HttpTransport:
    """
    Shared transport layer with exponential backoff, jitter, bounded retry budget,
    and structured D7 failure normalization.
    """

    def __init__(self, timeout: float = 30.0, max_retry_budget: float = MAX_TOTAL_RETRY_BUDGET):
        self.timeout = timeout
        self.max_retry_budget = max_retry_budget

    async def get(
        self,
        url: str,
        headers: Dict[str, str] = None,
        params: Dict[str, Any] = None,
    ) -> httpx.Response:
        return await self._request("GET", url, headers=headers, params=params)

    async def post(
        self,
        url: str,
        data: Dict[str, Any] = None,
        headers: Dict[str, str] = None,
    ) -> httpx.Response:
        return await self._request("POST", url, data=data, headers=headers)

    async def stream_lines(
        self,
        url: str,
        headers: Dict[str, str] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Streams a remote resource line-by-line through the shared transport.
        Used for NDJSON Bulk Data output files.
        Enforces shared timeout, jitter, and bounded retry budget.
        """
        max_attempts = 3
        attempt = 0
        backoff = 1.0
        start_time = time.time()

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            while attempt < max_attempts:
                attempt += 1
                try:
                    async with client.stream("GET", url, headers=headers or {}) as response:
                        if response.status_code in (401, 403):
                            raise ConnectorException(
                                f"Authentication failed: HTTP {response.status_code}",
                                failure_code="AUTHENTICATION_FAILED",
                            )

                        if response.status_code == 429:
                            retry_after = parse_retry_after(response.headers.get("Retry-After"), backoff)
                            raw_delay = retry_after + random.uniform(0, min(retry_after, 5.0) * 0.2)
                            delay = min(raw_delay, MAX_INDIVIDUAL_DELAY)
                            elapsed = time.time() - start_time
                            if (elapsed + delay) > self.max_retry_budget or attempt >= max_attempts:
                                raise RateLimitedException(retry_after)
                            logger.warning(f"Rate limited. Waiting {delay:.2f}s.")
                            await asyncio.sleep(delay)
                            backoff = min(backoff * 2, MAX_INDIVIDUAL_DELAY)
                            continue

                        if response.status_code >= 500:
                            raw_delay = backoff + random.uniform(0, backoff * 0.2)
                            delay = min(raw_delay, MAX_INDIVIDUAL_DELAY)
                            elapsed = time.time() - start_time
                            if (elapsed + delay) > self.max_retry_budget or attempt >= max_attempts:
                                raise ConnectorException(
                                    f"Server error {response.status_code}",
                                    failure_code="RESPONSE_INVALID",
                                )
                            logger.warning(
                                f"Server error {response.status_code}. Retrying in {delay:.2f}s..."
                            )
                            await asyncio.sleep(delay)
                            backoff = min(backoff * 2, MAX_INDIVIDUAL_DELAY)
                            continue

                        if response.is_error:
                            raise ConnectorException(
                                f"HTTP error {response.status_code}",
                                failure_code="RESPONSE_INVALID",
                            )

                        async for line in response.aiter_lines():
                            if line.strip():
                                yield line
                        return

                except ConnectorException:
                    raise
                except httpx.TimeoutException as e:
                    elapsed = time.time() - start_time
                    if attempt >= max_attempts or elapsed >= self.max_retry_budget:
                        raise ConnectorException(f"Request timeout: {str(e)}", failure_code="TIMEOUT")
                    delay = min(backoff + random.uniform(0, backoff * 0.2), MAX_INDIVIDUAL_DELAY)
                    await asyncio.sleep(delay)
                    backoff = min(backoff * 2, MAX_INDIVIDUAL_DELAY)
                except (httpx.ConnectError, httpx.NetworkError) as e:
                    elapsed = time.time() - start_time
                    if attempt >= max_attempts or elapsed >= self.max_retry_budget:
                        raise ConnectorException(f"Network unavailable: {str(e)}", failure_code="NETWORK_UNAVAILABLE")
                    delay = min(backoff + random.uniform(0, backoff * 0.2), MAX_INDIVIDUAL_DELAY)
                    await asyncio.sleep(delay)
                    backoff = min(backoff * 2, MAX_INDIVIDUAL_DELAY)
                except httpx.RequestError as e:
                    elapsed = time.time() - start_time
                    if attempt >= max_attempts or elapsed >= self.max_retry_budget:
                        raise ConnectorException(f"Network error: {str(e)}", failure_code="NETWORK_UNAVAILABLE")
                    delay = min(backoff + random.uniform(0, backoff * 0.2), MAX_INDIVIDUAL_DELAY)
                    await asyncio.sleep(delay)
                    backoff = min(backoff * 2, MAX_INDIVIDUAL_DELAY)

            raise ConnectorException("Max retries exceeded for stream", failure_code="NETWORK_UNAVAILABLE")

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        max_attempts = 3
        attempt = 0
        backoff = 1.0
        start_time = time.time()

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            while attempt < max_attempts:
                attempt += 1
                try:
                    response = await client.request(method, url, **kwargs)

                    if response.status_code in (401, 403):
                        raise ConnectorException(
                            f"Authentication failed: HTTP {response.status_code}",
                            failure_code="AUTHENTICATION_FAILED",
                        )

                    if response.status_code == 429:
                        retry_after = parse_retry_after(response.headers.get("Retry-After"), backoff)
                        raw_delay = retry_after + random.uniform(0, min(retry_after, 5.0) * 0.2)
                        delay = min(raw_delay, MAX_INDIVIDUAL_DELAY)
                        elapsed = time.time() - start_time
                        if (elapsed + delay) > self.max_retry_budget or attempt >= max_attempts:
                            raise RateLimitedException(retry_after)
                        logger.warning(f"Rate limited. Waiting {delay:.2f}s.")
                        await asyncio.sleep(delay)
                        backoff = min(backoff * 2, MAX_INDIVIDUAL_DELAY)
                        continue

                    if response.status_code >= 500:
                        raw_delay = backoff + random.uniform(0, backoff * 0.2)
                        delay = min(raw_delay, MAX_INDIVIDUAL_DELAY)
                        elapsed = time.time() - start_time
                        if (elapsed + delay) > self.max_retry_budget or attempt >= max_attempts:
                            raise ConnectorException(
                                f"Server error {response.status_code}",
                                failure_code="RESPONSE_INVALID",
                            )
                        logger.warning(
                            f"Server error {response.status_code}. Retrying in {delay:.2f}s..."
                        )
                        await asyncio.sleep(delay)
                        backoff = min(backoff * 2, MAX_INDIVIDUAL_DELAY)
                        continue

                    if response.is_error:
                        raise ConnectorException(
                            f"HTTP error {response.status_code}",
                            failure_code="RESPONSE_INVALID",
                        )

                    return response

                except ConnectorException:
                    raise
                except httpx.TimeoutException as e:
                    elapsed = time.time() - start_time
                    if attempt >= max_attempts or elapsed >= self.max_retry_budget:
                        raise ConnectorException(f"Request timeout: {str(e)}", failure_code="TIMEOUT")
                    delay = min(backoff + random.uniform(0, backoff * 0.2), MAX_INDIVIDUAL_DELAY)
                    await asyncio.sleep(delay)
                    backoff = min(backoff * 2, MAX_INDIVIDUAL_DELAY)
                except (httpx.ConnectError, httpx.NetworkError) as e:
                    elapsed = time.time() - start_time
                    if attempt >= max_attempts or elapsed >= self.max_retry_budget:
                        raise ConnectorException(f"Network unavailable: {str(e)}", failure_code="NETWORK_UNAVAILABLE")
                    delay = min(backoff + random.uniform(0, backoff * 0.2), MAX_INDIVIDUAL_DELAY)
                    await asyncio.sleep(delay)
                    backoff = min(backoff * 2, MAX_INDIVIDUAL_DELAY)
                except httpx.RequestError as e:
                    elapsed = time.time() - start_time
                    if attempt >= max_attempts or elapsed >= self.max_retry_budget:
                        raise ConnectorException(f"Network error: {str(e)}", failure_code="NETWORK_UNAVAILABLE")
                    delay = min(backoff + random.uniform(0, backoff * 0.2), MAX_INDIVIDUAL_DELAY)
                    await asyncio.sleep(delay)
                    backoff = min(backoff * 2, MAX_INDIVIDUAL_DELAY)

            raise ConnectorException("Max retries exceeded", failure_code="NETWORK_UNAVAILABLE")
