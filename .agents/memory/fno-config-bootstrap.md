---
name: fno_config table bootstrap
description: Where the F&O config DB schema is actually created/migrated.
---

The `fno_config` table (Admin → F&O Settings) is created and migrated by an
inline list of SQL statements in `app.py` (the same block that bootstraps
`data_source_config`, `fno_signal_history`, etc.), executed on every startup.

**Why:** `services/fno_config.py` defines a `bootstrap_fno_config()` function,
but it is never called anywhere. Editing only that function will NOT change the
deployed schema. Schema changes must be made in the app.py bootstrap SQL list
(use additive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for backward compat).

**How to apply:** When adding/altering F&O config columns, edit the app.py
bootstrap block, not just the fno_config service. Verify with a psql SELECT after
a restart; the startup log line "Incremental column migrations: N ok" confirms
the migration list ran.
