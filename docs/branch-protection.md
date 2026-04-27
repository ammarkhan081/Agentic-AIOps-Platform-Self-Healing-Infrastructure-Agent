# Branch Protection Recommendations

Recommended protected branch: `main`

## Required settings

- Require a pull request before merging
- Require at least 1 approval
- Dismiss stale approvals when new commits are pushed
- Require status checks to pass before merging
- Require branches to be up to date before merging
- Require conversation resolution before merging
- Restrict direct pushes to `main`

## Required status checks

Use the workflow checks from `.github/workflows/ci.yml`:

- `Validate Gate`
- `Backend Tests`

## Recommended merge strategy

- Enable squash merging
- Disable merge commits unless you explicitly want multi-commit history on `main`

## Administrative guidance

- Add real usernames to `.github/CODEOWNERS`
- Replace placeholder owners before enabling required review from code owners

