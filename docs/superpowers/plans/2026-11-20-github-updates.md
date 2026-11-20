# GitHub release update plan

Goal: Read veresse/rikkahub GitHub releases directly over HTTPS without a companion server.

Design approved in conversation: Map GitHub tag, publication date, notes and APK assets into the existing UpdateInfo model. Preserve the UI and download flow. Custom build suffixes are metadata rather than prerelease labels. Future releases still need increasing APK versionName/versionCode.

- [x] Test GitHub field mapping, null notes, APK filtering, empty assets and custom tags.
- [x] Implement conversion and direct GitHub request with useful HTTP errors and response cleanup.
- [x] Run focused tests and inspect changes; report any environment blockers.

Scope excludes CI synchronization changes, server deployment, pushing and publishing releases.

Verification: Gradle compiled app and test sources successfully, but its test executor failed to load GradleWorkerMain before executing assertions. Running the compiled tests through JUnitCore passed all 14 tests (GitHubReleaseTest and VersionTest). git diff --check passed. The live latest-release API returned the current custom release and three APKs.
