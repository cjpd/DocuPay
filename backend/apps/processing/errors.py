class ProcessingError(Exception):
    """Base error for the document pipeline."""


class TransientProcessingError(ProcessingError):
    """A retry can succeed (rate limit, provider outage, network error)."""


class PermanentProcessingError(ProcessingError):
    """A retry cannot succeed (bad file, bad request, auth error, refusal)."""


class ModelOutputError(PermanentProcessingError):
    """The model answered but the answer is unusable (invalid JSON, cut off, refusal).
    A different (stronger) model can still succeed, so the pipeline escalates."""
