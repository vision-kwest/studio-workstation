"""Command-scoped stderr reporting; never runs or changes an AWS operation."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import re
import sys
from threading import Event, Lock, Thread
import time

_current = ContextVar("studio_progress", default=None)
_STATES = set(("pending running stopping stopped shutting-down terminated "
               "Pending InProgress Delayed Success Cancelled Cancelling Failed TimedOut "
               "CREATE_IN_PROGRESS CREATE_COMPLETE CREATE_FAILED DELETE_IN_PROGRESS "
               "DELETE_COMPLETE DELETE_FAILED UPDATE_IN_PROGRESS UPDATE_COMPLETE UPDATE_FAILED "
               "ROLLBACK_IN_PROGRESS ROLLBACK_COMPLETE ROLLBACK_FAILED "
               "UPDATE_ROLLBACK_IN_PROGRESS UPDATE_ROLLBACK_COMPLETE UPDATE_ROLLBACK_FAILED").split())


class _Reporter:
    def __init__(self, action, scope, interval):
        self.action, self.scope = action, re.sub(r"[^A-Za-z0-9:/_.-]", "?", scope)[:200]
        self.interval, self.started, self.stream = interval, time.monotonic(), sys.stderr
        self.stop, self.lock = Event(), Lock()
        self.stage, self.last, self.observations = "Validating request", "not observed", {}

    def _write(self, event):
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            print(f"[{timestamp}] {self.action} scope={self.scope} "
                  f"elapsed={time.monotonic() - self.started:.1f}s stage={self.stage} "
                  f"last={self.last} | {event}", file=self.stream, flush=True)
        except (OSError, ValueError):
            pass  # A closed progress pipe must never interrupt cleanup or rollback.

    def emit(self, event):
        with self.lock:
            self._write(event)

    def update(self, label):
        with self.lock:
            if label != self.stage:
                self.stage = label
                self._write("progress")

    def observed(self, service, value):
        with self.lock:
            self.last = f"{service}:{value}"
            if self.observations.get(service) != value:
                self.observations[service] = value
                self._write("observed")

    def heartbeat(self):
        while not self.stop.wait(self.interval):
            self.emit("heartbeat")


@contextmanager
def operation(action, scope, *, enabled=True, interval=10):
    reporter = _Reporter(action, scope, interval) if enabled else None
    token = _current.set(reporter)
    try:
        if reporter is None:
            yield
            return
        worker = Thread(target=reporter.heartbeat, name="studio-progress", daemon=True)
        outcome = "command finished"
        reporter.emit("started")
        worker.start()
        try:
            yield
        except BaseException as exc:
            outcome = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            raise
        finally:
            reporter.stop.set()
            worker.join()
            reporter.emit(outcome)
    finally:
        _current.reset(token)


def stage(label):
    if reporter := _current.get():
        reporter.update(label)


def observe(service, response):
    """Read only documented state fields; never log argv, tags or guest output."""
    reporter = _current.get()
    if reporter is None or not isinstance(response, dict):
        return
    values = []
    if service == "ssm":
        values = [response.get("Status"), response.get("Command", {}).get("Status")]
    elif service == "cloudformation":
        values = [s.get("StackStatus") for s in response.get("Stacks", [])]
    elif service == "ec2":
        values = [i.get("State", {}).get("Name") for r in response.get("Reservations", [])
                  for i in r.get("Instances", [])]
        values += [i.get("CurrentState", {}).get("Name")
                   for key in ("StartingInstances", "StoppingInstances", "TerminatingInstances")
                   for i in response.get(key, [])]
    for value in values:
        if isinstance(value, str) and value in _STATES:
            reporter.observed(service, value)
