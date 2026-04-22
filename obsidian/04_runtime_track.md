# Runtime Track

Main files:
- `sentineltest.py`
- `sentinel_runtime/config.py`
- `sentinel_runtime/runtime.py`
- `sentinel_runtime/preflight.py`
- `sentinel_runtime/storage.py`

Key promises:
- safe preflight before launch
- dry-run by default
- local persistence and clearer operator flow

Key demo command:
- `python3 sentineltest.py --preflight`

Related:
- [[02_current_state]]
- [[06_risks_and_open_questions]]
- [[09_commands_and_entrypoints]]
