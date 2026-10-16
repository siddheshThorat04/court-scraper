class Retry(Exception):
    """Task failed in a recoverable way; leave it un-acked so it is retried."""


class RateLimited(Retry):
    """The site's search API answered HTTP 429 (or another error). The page may still SHOW 'Total Record Count: 0',
    so this must never be mistaken for 'no results'."""


class Stop(Exception):
    """Graceful shutdown requested while a task was in progress."""
