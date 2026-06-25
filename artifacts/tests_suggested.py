"""
Issue 1454 test generator note.

No real test file was added for this task. The patch removes a duplicate static
title/subtitle block from a large client component and does not change state,
data fetching, permissions, or business logic.

Recommended regression signal:
- browser/staging visual check for /settings/users with an authenticated
  manage_users session;
- verify Benutzerverwaltung appears once in the PageHeader;
- verify Mitarbeiter/Kunden/Affiliates tabs and Deine Rolle badge remain usable.
"""
