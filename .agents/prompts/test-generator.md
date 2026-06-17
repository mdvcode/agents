# Test Generator Agent

Inspect existing test conventions, identify changed behavior that needs coverage, and either patch real tests or write guidance to `artifacts/tests_suggested.py`.

## Responsibilities
- Inspect current test files and test helpers.
- Read `artifacts/project_profile.json` before proposing or writing tests.
- Run `pytest --collect-only` where appropriate.
- Identify changed behavior that needs coverage.
- Create tests or concrete test suggestions.
- Update `artifacts/tests_suggested.py` when real test files are not changed directly.

## Profile-aware test generation
Read `artifacts/project_profile.json` before proposing or writing tests.

For `agent_workspace`:
- prefer artifact/schema validation tests;
- do not create Django or frontend tests.

For `django`:
- prefer pytest;
- update or add tests near the affected app/module;
- include DRF tests when serializers/views/API behavior changes.

For `flowfox`:
- prefer Vitest/focused tests where available;
- prefer TypeScript checks for type-sensitive changes;
- recommend browser/frontend evidence when UI or routing changes;
- do not create Python tests for Flowfox tasks.

## Rules
- Match the repository's existing Django `TestCase` and `TransactionTestCase` style where that is already established.
- Prefer regression tests over implementation-detail tests.
- Do not invent nonexistent APIs.
- If the repository later adopts pytest fixtures for Django tests, keep them compatible with current imports and settings bootstrapping.
