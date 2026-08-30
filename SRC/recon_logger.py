"""
recon_logger.py
----------------
Structured (JSON-lines) logging for the reconciliation pipeline, so that
each run's events -- start, data loaded, validated, matched, completed --
are queryable by a monitoring/alerting system, not just human-readable text.

This does NOT replace the existing print() statements in report.py/matcher.py
(those stay, for a human running it in a terminal). This is an ADDITIONAL,
parallel structured log written to output/reconciliation.log, one JSON
object per line, so it's easy to grep, parse, or feed into a log aggregator
(e.g. `jq '.event' output/reconciliation.log` or ship it to a real
observability platform).

Usage:
    from recon_logger import get_logger
    log = get_logger()
    log.info("reconciliation_started", settlement_path=..., ledger_path=...)
    log.info("data_loaded", ledger_rows=220, settlement_rows=218, load_time_s=0.03)
    log.warning("high_exception_rate", exception_pct=45.2)
    log.error("validation_failed", reason=str(e))
"""

import json
import logging
import os
import sys
import time


LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "reconciliation.log")


class JsonFormatter(logging.Formatter):
    """Formats each log record as a single JSON line: timestamp, level,
    event name, and any extra structured fields passed via log.info(event, **fields)."""

    def format(self, record):
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        # Any extra fields passed as kwargs land in record.__dict__ via the
        # StructuredLogger wrapper below (see _log()).
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        return json.dumps(payload)


class StructuredLogger:
    """Thin wrapper around the stdlib logger so callers can write
    log.info("event_name", key=value, key2=value2) instead of manually
    building a dict every time."""

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def _log(self, level, event, **fields):
        record = self._logger.makeRecord(
            self._logger.name, level, "(recon)", 0, event, (), None
        )
        record.extra_fields = fields
        self._logger.handle(record)

    def debug(self, event, **fields):
        self._log(logging.DEBUG, event, **fields)

    def info(self, event, **fields):
        self._log(logging.INFO, event, **fields)

    def warning(self, event, **fields):
        self._log(logging.WARNING, event, **fields)

    def error(self, event, **fields):
        self._log(logging.ERROR, event, **fields)


_logger_instance = None


def get_logger(level=logging.INFO) -> StructuredLogger:
    """Returns a singleton StructuredLogger that writes JSON lines to
    output/reconciliation.log (and also echoes WARNING+ to stderr, so
    problems are visible even if nobody's tailing the log file)."""
    global _logger_instance
    if _logger_instance is not None:
        return _logger_instance

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

    base_logger = logging.getLogger("reconciliation")
    base_logger.setLevel(level)
    base_logger.propagate = False

    if not base_logger.handlers:
        file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        base_logger.addHandler(file_handler)

        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.WARNING)
        stderr_handler.setFormatter(JsonFormatter())
        base_logger.addHandler(stderr_handler)

    _logger_instance = StructuredLogger(base_logger)
    return _logger_instance


class timed_stage:
    """Context manager that logs a stage's start/end and elapsed time.

    Usage:
        with timed_stage(log, "load_data"):
            settlement, ledger = load_data(...)
    """

    def __init__(self, log: StructuredLogger, stage_name: str, **context):
        self.log = log
        self.stage_name = stage_name
        self.context = context

    def __enter__(self):
        self.start = time.time()
        self.log.info(f"{self.stage_name}_started", **self.context)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed = round(time.time() - self.start, 3)
        if exc_type is None:
            self.log.info(f"{self.stage_name}_completed", elapsed_seconds=elapsed, **self.context)
        else:
            self.log.error(f"{self.stage_name}_failed", elapsed_seconds=elapsed,
                            error=str(exc_val), **self.context)
        return False  # don't suppress exceptions
