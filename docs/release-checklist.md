# Release Checklist

## Engineering

- [ ] `npm run validate`
- [ ] `cd aiops && python -m pytest -q`
- [ ] frontend production build succeeds
- [ ] backend API contracts still match frontend expectations

## Documentation

- [ ] `README.md` is current
- [ ] `CHANGELOG.md` includes the release summary
- [ ] deployment notes reflect the latest runtime expectations

## Security and config

- [ ] no secrets committed
- [ ] `.env.example` still reflects required variables
- [ ] Docker/runtime config reviewed

## Submission / demo readiness

- [ ] LangSmith link updated
- [ ] demo video link updated
- [ ] deployment link updated
- [ ] screenshots / demo flow still match the product

