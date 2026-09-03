"""Background work: long tasks, automations, memory maintenance, health checks.

Importing this package registers the job catalogue. A registry that only populates when
someone remembers to import the right module is a registry that fails in production and
passes in tests.
"""

from thursday_worker import jobs as _jobs  # noqa: F401  (import for its side effect)
from thursday_worker.queue import JOBS, build_queue, job

__all__ = ["JOBS", "build_queue", "job"]
