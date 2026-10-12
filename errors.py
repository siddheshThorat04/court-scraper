class Retry(Exception):
    """Task failed in a recoverable way; leave it un-acked so it is retried."""


class Stop(Exception):
    """Graceful shutdown requested while a task was in progress."""
