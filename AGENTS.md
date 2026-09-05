# Providency repository contract

- Work directly on `main`; do not create branches or worktrees.
- Complete and verify one development increment before pushing.
- Keep development plans outside this repository.
- Keep secrets and operational data outside Git.
- Start in `DRY_RUN`; demo execution requires its own increment.
- Never enable live-money execution without a separate task and explicit human approval.
- Only `VectorAdapter` may interact with Vector Web.
- Ambiguous visual or account state must fail closed.
