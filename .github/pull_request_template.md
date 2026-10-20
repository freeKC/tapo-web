## What and why

<!-- What does this change and why? Link the issue if there is one. -->

## Checklist

- [ ] Tests pass: `python -m pytest tests -q` (it runs `node --test tests/test_i18n.cjs` too when Node is installed)
- [ ] New code comes with offline tests (fakes in `tests/`, no camera needed)
- [ ] Live tests on a real camera, if any: model and firmware: <!-- e.g. C510W 1.3.4, or "none" -->
- [ ] No passwords, tokens, IP or MAC addresses in the code, logs or test fixtures
- [ ] New UI strings are in every file in `app/static/locales/`
