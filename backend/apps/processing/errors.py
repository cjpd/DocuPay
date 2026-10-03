class ProcessingError(Exception):
    """Base error for the document pipeline."""


class TransientProcessingError(ProcessingError):
    """A retry can succeed (rate limit, provider outage, network error)."""


class PermanentProcessingError(ProcessingError):
    """A retry cannot succeed (bad file, bad request, auth error, refusal)."""
