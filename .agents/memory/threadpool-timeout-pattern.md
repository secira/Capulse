---
name: ThreadPoolExecutor timeout pattern
description: Correct pattern for adding a hard timeout around a potentially-hanging SDK call in a Flask route.
---

## Rule

Never use `with ThreadPoolExecutor() as pool:` when you need a hard timeout on a blocking call.

**Why:** `ThreadPoolExecutor.__exit__` calls `shutdown(wait=True)`, which blocks the calling thread indefinitely waiting for any still-running futures — even AFTER `future.result(timeout=N)` has already raised `concurrent.futures.TimeoutError` and you've returned a response. The context manager itself becomes the hang.

This was observed in the Dhan broker SDK integration: the Flask route returned the `TimeoutError` response inside the `with` block, but the `with` block's `__exit__` then stalled the gunicorn worker for 30+ seconds waiting for the hung SDK thread.

**How to apply:**

Whenever you need `ThreadPoolExecutor` with a hard timeout in a Flask route:

```python
import concurrent.futures as _cf

_pool = _cf.ThreadPoolExecutor(max_workers=1)
_fut = _pool.submit(some_blocking_function, arg1, arg2)
try:
    result = _fut.result(timeout=22)
    _pool.shutdown(wait=False)   # success path — release without waiting
except _cf.TimeoutError:
    _pool.shutdown(wait=False)   # timeout path — orphan the hung thread, don't block
    return jsonify({'success': False, 'error': '...'}), 504
```

`shutdown(wait=False)` tells Python not to block on the background thread. The gunicorn worker remains available for the next request. The orphaned thread will eventually be killed when the worker is recycled or the OS closes the TCP connection.
