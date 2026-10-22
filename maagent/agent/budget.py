"""Cooperative per-command deadline; no orphan background game actions."""
import time
from contextvars import ContextVar
from functools import wraps

_deadline = ContextVar('agent_deadline', default=None)


def remaining(cap=600.0):
    deadline = _deadline.get()
    value = min(cap, deadline - time.monotonic()) if deadline is not None else cap
    if value <= 0:
        raise TimeoutError('本轮已达到设定执行时限（最多10分钟）')
    return max(0.01, value)


def bounded_turn(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        token = _deadline.set(time.monotonic() + min(600, getattr(self, 'task_timeout_seconds', 600)))
        try:
            return method(self, *args, **kwargs)
        except TimeoutError:
            return self._stop_result('达到执行时限，已停止继续操作')
        finally:
            _deadline.reset(token)
    return wrapped
