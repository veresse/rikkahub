# Automatic App Updates Implementation Plan

**Goal:** Publish on main pushes and let users discover, download and install updates from the app.
**Architecture:** GitHub Actions publishes signed APKs. Foreground UI and periodic WorkManager jobs share the release fetcher. A dedicated activity owns download progress and installation; a download receiver posts a continuation notification when backgrounded.
**Tech Stack:** Kotlin, Compose Material 3 Expressive, WorkManager, DownloadManager, FileProvider, GitHub Actions.
**Spec:** User approved the preceding proposed flow and delegated implementation choices. Updates require the Android installation confirmation. Old clients with a broken update URL require one bootstrap installation.

## Constraints and review focus

- Respect Play flavor exclusion, onboarding and disabled-until setting.
- Never start an installer from a background receiver; notification opens the activity.
- Persist download identity across process death; prevent duplicate active downloads.
- Choose ABI-compatible APKs, verify downloaded package/version/signing certificate before installation.
- Permission denial must leave a usable retry/install path. Notification denial must not block foreground updates.
- Serialize publishing without cancelling running builds. Retain reusable sync workflow; bot pushes do not trigger duplicate builds.

## Tasks

- [x] 1. Add release/ABI policy tests, observe missing implementation, implement policy; enable main push builds and serialize publication. Validate actionlint.
- [x] 2. Share fetcher; implement periodic update worker and foreground prompt. Respect settings, handle errors/cancellation and deduplicate notifications. Compile.
- [x] 3. Implement update activity, persisted DownloadManager download, receiver notification, unknown-source permission return and validated FileProvider install. Compile both flavors.
- [x] 4. Run targeted tests, workflow checks and APK build; review complete change, fix material findings; prepare authorized commit/push. Report device verification limits.

## Progress / decisions

- Working in the existing authorized main workspace with a clean starting tree at e489ff44. Continue inline using writing-plans, executing-plans and TDD; no additional approval round because user explicitly said to implement the proposed approach.
- Policy tests observed missing selectUpdateDownload/shouldCheckForUpdate before implementation. GitHub/Play Kotlin compilation and 24 targeted JUnit tests passed before final review fixes.
- Independent review found receiver export, awaitLoaded and permission recreation issues. Fixed with signature-protected receiver, SettingsStore.awaitLoaded and saved pending action. Device tests unavailable (adb has no devices).
- Ruling: keep existing signing key and reject different signer sets; signing-key rotation is outside this delivery.
- Final verification: assembleGithubDebug, compileGithubDebugUnitTestKotlin and compilePlayDebugKotlin BUILD SUCCESSFUL (17s). JUnitCore: 24 tests passed. actionlint passed. Standard testGithubDebugUnitTest could not start Gradle test workers (ClassNotFoundException GradleWorkerMain); no claim that full suite passed. Real installation and background notification verification remain in docs/app-updates.md.
