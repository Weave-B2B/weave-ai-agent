"""Request-scoped hook for accounting each provider attempt, including retries."""
from contextlib import contextmanager
from contextvars import ContextVar

_reserve = ContextVar("reserve_llm_attempt", default=None)


@contextmanager
def account_attempts(reserve):
    token = _reserve.set(reserve)
    try:
        yield
    finally:
        _reserve.reset(token)


def before_attempt():
    callback = _reserve.get()
    if callback:
        callback()
