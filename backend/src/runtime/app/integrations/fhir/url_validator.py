from urllib.parse import urljoin, urlparse


class UntrustedExternalUrl(Exception):
    """Raised when an external FHIR URL is outside the approved FHIR origin."""
    pass


# Backward compatibility alias
UntrustedPaginationUrl = UntrustedExternalUrl


def validate_fhir_url(candidate_url: str, approved_base_url: str) -> str:
    """
    Validate that candidate_url resolves within the approved origin of approved_base_url.
    Guarantees Bearer credentials are never forwarded to an unapproved external origin.
    Applies to:
    - Bundle 'next' pagination links
    - Bulk export Content-Location
    - Bulk export polling/status URLs
    - Bulk export manifest output NDJSON URLs
    """
    if not candidate_url:
        raise UntrustedExternalUrl("Candidate URL cannot be empty")
    if not approved_base_url:
        raise UntrustedExternalUrl("Approved base URL cannot be empty")

    resolved = urljoin(approved_base_url, candidate_url)

    approved = urlparse(approved_base_url)
    candidate = urlparse(resolved)

    if (
        candidate.scheme != approved.scheme
        or candidate.netloc != approved.netloc
    ):
        raise UntrustedExternalUrl(
            f"External URL is outside approved FHIR origin ({candidate.scheme}://{candidate.netloc} != {approved.scheme}://{approved.netloc})"
        )

    return resolved
