# Manual auto-chat and upstream sync fixes

## Evidence and approach

Manual tests reuse slot -1, violating the occurrence unique index; INSERT IGNORE then hides the failed insert. Allocate decreasing negative slots transactionally and execute against the supplied draft rule. Preserve scheduled execution and existing saved settings.

The fork initial commit 181b1a6c has the same application source as upstream 5863c4e4c2fa10111d4a8ae0867d9ba1f5ec76cf. Only nine repository configuration, symlink and mode differences exist. Establish this verified baseline as ancestry while preserving the fork tree, then merge current upstream normally. Never mark a newer upstream revision integrated without applying its changes.

The fork and upstream both used database version 33 for different schemas. Preserve the released fork schema and provide a forward migration for upstream changes without deleting user data.

Invoke the release workflow as a reusable job after successful sync, since GITHUB_TOKEN tag pushes do not trigger other push workflows. Propagate the exact merged revision. Set APK and release versions together; use seconds since 2020 as versionCode and as a fourth versionName component to support future same-base builds.

## Checklist

- [x] Diagnose manual test insertion and upstream ancestry.
- [x] Add manual occurrence persistence regression tests and fix draft execution.
- [x] Update reusable build and synchronization workflows; actionlint passes.
- [x] Integrate current upstream and preserve database migration compatibility.
- [x] Compile, execute JVM tests, validate workflow syntax and review diff.
- [ ] Commit and push fixes to the user repository.

Verification: combined app and unit-test Kotlin compilation succeeded; JUnitCore passed 19 manual-test/version/parser tests using the project's exact runtime dependency versions. Six Node SQLite checks passed, including generated Room 35 schema comparison and data preservation. actionlint passed both workflows; upstream/master is an ancestor and behind count is zero. Android device instrumentation and remote workflow execution have not been performed.
