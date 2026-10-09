from typing import Dict, Any, List, AsyncGenerator
import logging
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)


class UntrustedPaginationUrl(Exception):
    pass


class BundlePager:
    """
    Handles generic FHIR Bundle pagination using standard 'next' links.
    """

    def __init__(self, transport, headers: Dict[str, str], auth=None):
        self.transport = transport
        self.headers = headers
        self.auth = auth

    async def fetch_all(self, initial_url: str, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Fetches all resources across all pages of a bundle.
        """
        resources = []
        async for batch in self.iterate(initial_url, params):
            resources.extend(batch)
        return resources

    async def iterate(self, initial_url: str, params: Dict[str, Any] = None) -> AsyncGenerator[List[Dict[str, Any]], None]:
        """
        Yields batches of resources from each page.
        """
        url = initial_url
        current_params = params
        parsed_init = urlparse(initial_url)
        approved_origin = (parsed_init.scheme, parsed_init.netloc)

        while url:
            if self.auth and hasattr(self.auth, "get_valid_token"):
                token_lease = await self.auth.get_valid_token(min_validity_seconds=30)
                self.headers["Authorization"] = f"Bearer {token_lease.access_token}"
            try:
                response = await self.transport.get(url, headers=self.headers, params=current_params)
                data = response.json()

                if data.get("resourceType") != "Bundle":
                    yield [data]
                    break

                entries = data.get("entry", [])
                resources = [entry.get("resource", {}) for entry in entries if entry.get("resource")]

                if resources:
                    yield resources

                # Find next page link
                next_link = next((link.get("url") for link in data.get("link", []) if link.get("relation") == "next"), None)
                if next_link:
                    next_url = urljoin(url, next_link)
                    if approved_origin[0] and approved_origin[1]:
                        parsed_next = urlparse(next_url)
                        if (parsed_next.scheme, parsed_next.netloc) != approved_origin:
                            raise UntrustedPaginationUrl(f"Cross-origin pagination link rejected: {next_url}")
                    url = next_url
                else:
                    url = None

                current_params = None  # Params are typically embedded in the next link

            except Exception as e:
                logger.error(f"Error paginating FHIR bundle at {url}: {e}")
                raise e
