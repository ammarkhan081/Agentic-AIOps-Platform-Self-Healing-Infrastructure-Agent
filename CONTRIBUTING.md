# Contributing

## Workflow

1. Create a feature branch from `main`
2. Make a focused change
3. Run the shared engineering gate:

```bash
npm run validate
```

4. For backend behavior changes, also run:

```bash
cd aiops
python -m pytest -q
```

5. Open a pull request using the repository PR template

## Branch Naming

Use one of these prefixes:

- `feature/<short-name>`
- `fix/<short-name>`
- `chore/<short-name>`
- `docs/<short-name>`

## Commit Hygiene

Recommended commit style:

- `feat: ...`
- `fix: ...`
- `chore: ...`
- `docs: ...`
- `refactor: ...`
- `test: ...`

## Pull Request Expectations

Every PR should include:

- a short summary of the change
- validation notes
- risk level
- screenshots for UI changes
- any follow-up work that is intentionally deferred

## Merge Policy

- Prefer squash merge for feature work
- Keep `main` always releasable
- Do not merge if CI is failing
- Require review for shared workflow, auth, incident orchestration, persistence, and remediation changes

## Release Hygiene

Before cutting a release:

1. Run `npm run validate`
2. Run full backend tests
3. Confirm README, changelog, and environment docs are current
4. Confirm placeholder links are either replaced or explicitly tracked as pending
5. Review secrets and `.env` handling

