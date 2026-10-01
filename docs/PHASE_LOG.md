# Phase Log

Work runs against the local Postgres database `arthasignal` only.

## Phase 1 - Scheduled workflows stopped

- `daily_pipeline.yml`, `intraday_pipeline.yml`, `fundamentals_pipeline.yml`: `schedule` removed; trigger is now `workflow_dispatch` only.
- `ci.yml`: `push` and `workflow_dispatch` removed; trigger is `pull_request` only. CI uses its own throwaway Postgres service container and never touches a real database.
- Verified by parsing every workflow with PyYAML: daily, intraday and fundamentals list only `workflow_dispatch`; ci lists only `pull_request`. No `schedule` or `push` key remains.
- Nothing writes to a database unless someone starts a pipeline by hand from the Actions tab. The `gh` CLI is not installed here, so runs already queued on GitHub could not be checked from this machine.
