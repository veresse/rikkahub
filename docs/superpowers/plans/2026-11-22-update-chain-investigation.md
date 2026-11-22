# Update-chain investigation and fixes

Evidence: successful runs 44839512607 and 44875252494 skipped merge and build because no upstream commits were available. No release was published on November 21. Release v3.3.1.217352194 was published November 20 15:46 UTC; downloaded APK manifest confirms versionName 3.3.1.217352194 and versionCode 217352194.

Run 44938067710 failed because upstream version bump 2435dbdf overlaps custom version-property assignments in app/build.gradle.kts. Move overrides away from upstream version declarations, retain both upstream defaults and CI properties. Merge the outstanding upstream commits normally.

UpdateChecker runs a finite flow with SharingStarted.Lazily, caching success/error for the process lifetime. Replace with cancellable polling: check immediately, repeat after one hour on success, retry after five minutes on failure. Stop after subscribers leave and reset replay so returning to chat checks again. Existing UI remains an in-app drawer card; no system push notification is promised. Network access, installed version and disabled-update settings remain device-specific factors.

- [x] Inspect remote runs, release metadata, actual APK manifest and merge conflict.
- [x] Merge upstream and isolate version overrides.
- [x] Add and execute refresh/retry regression tests.
- [x] Validate future upstream version bumps, CI syntax, Kotlin compile and tests.
- [ ] Push fixes and explain release/install steps and verification limits.

Verification: Kotlin app and test compilation passed. JUnitCore passed 22 targeted tests including virtual-time refresh/retry and actual release-version comparison. Six SQLite checks and actionlint passed. Synthetic upstream 3.3.2/versionCode199 merged cleanly via merge-tree (only upstream version lines changed). Current upstream is fully integrated. No device-specific logs were available; installed package vintage, disabled checks and network cannot be confirmed remotely.
