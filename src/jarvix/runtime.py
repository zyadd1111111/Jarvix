"""Per-operation cancellation and approval context, shared by nested capabilities."""
from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Event

from jarvix.domain import Approval, EventSink
from typing import Callable


@dataclass
class ExecutionContext:
    cancel: Event
    approve: Approval
    deadline: float
    steps_remaining: int = 32
    unattended: bool = False
    approved_app: tuple | None = None
    on_event: EventSink | None = None
    checkpoint: Callable[[], None] | None = None


CURRENT = contextvars.ContextVar("jarvix_execution", default=None)


def check_cancelled():
    current = CURRENT.get()
    if current and current.checkpoint:
        current.checkpoint()
    if current and (current.cancel.is_set() or time.monotonic() > current.deadline):
        raise InterruptedError("Operation stopped or timed out.")


def consume_step():
    check_cancelled()
    current = CURRENT.get()
    if current:
        if current.steps_remaining <= 0:
            raise InterruptedError("Operation reached the nested action limit.")
        current.steps_remaining -= 1


@contextmanager
def operation(cancel=None, approve=None, timeout=120, max_steps=32, unattended=False,
              on_event=None, checkpoint=None):
    previous = CURRENT.get()
    if previous is not None:
        check_cancelled()
        previous_unattended = previous.unattended
        previous_event, previous_checkpoint = previous.on_event, previous.checkpoint
        previous.unattended = previous_unattended or unattended
        if on_event is not None:
            previous.on_event = on_event
        if checkpoint is not None:
            previous.checkpoint = checkpoint
        try:
            yield previous
        finally:
            previous.unattended = previous_unattended
            previous.on_event, previous.checkpoint = previous_event, previous_checkpoint
        return
    timeout = max(1, min(float(timeout), 600))
    context = ExecutionContext(cancel or Event(), approve or (lambda _: False),
                               time.monotonic() + timeout, max(1, min(int(max_steps), 64)), unattended)
    context.on_event, context.checkpoint = on_event, checkpoint
    token = CURRENT.set(context)
    try:
        check_cancelled()
        yield context
    finally:
        CURRENT.reset(token)
