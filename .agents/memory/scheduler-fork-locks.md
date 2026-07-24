---
name: Schedulers under gunicorn preload + advisory locks
description: Why background schedulers must start in post_fork, and how advisory locks must pin their connection
---

**Rule 1:** With `preload_app=True`, never start APScheduler/background threads at module import — they start in the gunicorn master and don't survive the fork. gunicorn.conf.py sets `SCHEDULERS_DEFERRED=1` and starts them per-worker in `post_fork`; app.py's import-time start is skipped when that var is set. Dev (no `-c` conf) still starts at import.

**Rule 2:** Postgres session-level advisory locks are released when the connection closes. The lock-holding connection must be `detach()`ed from the pool AND pinned in module-level state, or GC/pool-recycle silently releases the lock and duplicate schedulers start. Canonical helper: `fno_monitor._try_acquire_scheduler_lock` — all schedulers delegate to it.

**Rule 3:** Every scheduler needs a UNIQUE lock ID (fno=728193001, alert dispatcher=…002, iscore nightly=…003, partner payout=…005). A collision means whichever scheduler grabs the ID first permanently blocks the other from starting anywhere.

**Rule 4:** Module-level scheduler state (`_state` dicts) is only visible in the worker running the scheduler; admin status endpoints hit arbitrary workers. Persist status to the DB (`scheduler_state` table) and merge on read.

**How to verify:** `SELECT ((classid::bigint << 32) | objid::bigint), COUNT(*) FROM pg_locks WHERE locktype='advisory' GROUP BY 1` — exactly 1 holder per key.
