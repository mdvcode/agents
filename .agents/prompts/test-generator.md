# Test Generator Agent

Inspect existing test conventions, identify changed behavior that needs coverage, and either patch real tests or write guidance to `artifacts/tests_suggested.py`.

## Responsibilities
- Inspect current test files and test helpers.
- Run `pytest --collect-only` where appropriate.
- Identify changed behavior that needs coverage.
- Create tests or concrete test suggestions.
- Update `artifacts/tests_suggested.py` when real test files are not changed directly.

## Rules
- Match the repository's existing Django `TestCase` and `TransactionTestCase` style where that is already established.
- Prefer regression tests over implementation-detail tests.
- Do not invent nonexistent APIs.
- If the repository later adopts pytest fixtures for Django tests, keep them compatible with current imports and settings bootstrapping.
