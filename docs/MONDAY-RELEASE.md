# Monday integration candidate

This branch combines Jodi's kiosk integration with Justin's addon migration fixes at e54466e91e3cf848fcc4046dc1711ded988de0d5. The fixes are committed, not temporarily applied by the test runner.

The Odoo runtime is pinned to d40b7c8c5de81ce8d1528a6b31608468f5f30d4d. Only synthetic data is permitted in the rehearsal environment. Integration is disabled until explicitly configured with separate kiosk and staff credentials and authorized test-record scopes.

Run evidence must be associated with the exact commit. A successful native test run does not imply UI, offline, CRM AI or deployment readiness. No customer database or Justin working branch is modified.

Kiosk contract: select session, identify authorized student within the roster, validate, submit one idempotent command, render backend receipt, clear private state. Staff Member 360 reads the same Odoo attendance record. AI must not be necessary for check-in.
