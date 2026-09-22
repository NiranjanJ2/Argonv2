# Reliable Sync and Locking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make phone registration, lockdown, task focus state, todo updates, Classroom synchronization, and work-night due dates reliable under retries and background execution.

**Architecture:** Keep the existing server-owned desired state and phone-owned enforcement model. Repair each boundary in place: restore runnable Swift tests, make registration self-healing, return truthful lock scheduling results, attach stable idempotency keys to queued writes, and separate complete Classroom truth from its bounded display list.

**Tech Stack:** Python 3.13, Flask, SQLite, Swift 5.9, SwiftUI, DeviceActivity, XCTest.

**Spec:** `docs/superpowers/specs/2026-09-21-reliable-sync-locking-design.md`

## Global Constraints

- Do not add dependencies.
- Preserve `/v1` compatibility for already-installed clients.
- Never claim a phone lock is applied without a matching phone report.
- Do not hard-code course names; due-date behavior follows deadline shape.
- Every production behavior change starts with a failing regression test.

## Review Focus

- A server reset while the phone retains its old “token synced” flag must re-register the device.
- A DeviceActivity scheduling error or empty block selection must reach the server as a failed application.
- A request applied before its HTTP response is lost must be safe to retry without conflict or duplication.
- An outstanding assignment outside the display window must remain open in the database.
- Timed UTC deadlines and date-only work-night deadlines must not be shifted by the same rule.

---

### Task 1: Restore the Swift sync test target

**Files:**
- Modify: `ios/Package.swift`
- Test: `ios/Tests/ArgonSyncTests/ArgonSyncTests.swift`

**Interfaces:**
- Consumes: existing `ArgonLog` calls in `ArgonStore`.
- Produces: a compiling `ArgonSync` package and runnable existing tests.

- [ ] Run `cd ios && swift test` and retain the expected `ArgonLog` missing-symbol failure.
- [ ] Add the Foundation-compatible `ArgonLog.swift` source to the package target and remove it from exclusions if present.
- [ ] Run `cd ios && swift test`; require all existing tests to pass.
- [ ] Commit the isolated repair.

### Task 2: Self-healing phone registration and one app delegate

**Files:**
- Modify: `ios/app/Foqos/Argon/ArgonAppDelegate.swift`
- Modify: `ios/app/Foqos/Argon/ArgonPush.swift`
- Modify: `ios/app/Foqos/Argon/ArgonBridge.swift`
- Modify: `ios/app/Foqos/Argon/ArgonLockReconciler.swift`
- Modify: `ios/app/Foqos/foqosApp.swift`
- Test: `ios/Tests/ArgonSyncTests/ArgonSyncTests.swift`

**Interfaces:**
- Consumes: `ArgonClient.register(deviceToken:)`, stored base URL, API token, and APNs authorization status.
- Produces: `ArgonPush.registrationNeeded(storedReceipt:serverIdentity:) -> Bool` as a pure tested decision; one installed app-delegate reference; launch-time APNs registration and token upload retry.

- [ ] Add a failing pure Swift test proving a changed/missing server identity invalidates an old sync receipt.
- [ ] Run the focused Swift test and confirm it fails because the decision API is absent.
- [ ] Implement the smallest receipt comparison in `ArgonPush`; store no raw API token in the receipt.
- [ ] Add a failing test proving the same server identity and token can skip a redundant upload.
- [ ] Implement launch-time authorization inspection, `registerForRemoteNotifications`, and stored-token synchronization.
- [ ] Replace the constructed `static let shared = ArgonAppDelegate()` with a reference installed by the adaptor-managed delegate; update call sites.
- [ ] Run Swift tests and the complete iOS app build.
- [ ] Commit the registration/delegate repair.

### Task 3: Truthful lockdown scheduling and focus state

**Files:**
- Modify: `ios/app/Foqos/Argon/ArgonLockWindow.swift`
- Modify: `ios/app/Foqos/Argon/ArgonLockReconciler.swift`
- Test: add `ios/app/foqosTests/ArgonLockDecisionTests.swift`
- Modify: `ios/app/foqos.xcodeproj/project.pbxproj` only if Xcode does not auto-include the new test file.

**Interfaces:**
- Consumes: desired `ArgonLock`, selected blocking profile, `DeviceActivityCenter.startMonitoring` result.
- Produces: `ArgonLockWindow.ArmResult` with success or a user-readable failure; server reports matching that result.

- [ ] Add failing tests for an empty selection and for a scheduling failure result.
- [ ] Run the focused Xcode test and confirm the failures are behavioral.
- [ ] Make `arm` return a result, reject an empty selection, and propagate `startMonitoring` errors.
- [ ] Update reconciliation so future locks are acknowledged only after successful arming and failures call `reportLock` with the error.
- [ ] Add a test that an immediate live lock still reports actual shield state rather than mere schedule acceptance.
- [ ] Run focused tests and build every iOS target.
- [ ] Commit the lockdown repair.

### Task 4: Retry-safe todo and chat mutations

**Files:**
- Modify: `ios/app/Foqos/Argon/ArgonClient.swift`
- Modify: `ios/app/Foqos/Argon/ArgonModels.swift`
- Modify: `ios/app/Foqos/Argon/ArgonOutbox.swift`
- Modify: `argon/store.py`
- Modify: `argon/api.py`
- Test: module self-tests in `argon/store.py` and `argon/api.py`
- Test: `ios/Tests/ArgonSyncTests/ArgonSyncTests.swift`

**Interfaces:**
- Consumes: stable `PendingWrite.id`.
- Produces: `Idempotency-Key` on queued requests; SQLite-backed prior-response lookup; desired-state success for start/stop/complete/read.

- [ ] Add failing API tests: repeated start/stop/complete return success and repeated keyed task creation/message send mutates once.
- [ ] Run `python -m argon.api` and confirm each regression fails for the expected conflict/duplication.
- [ ] Add the minimal SQLite idempotency table and store methods, with bounded cleanup by creation time.
- [ ] Wrap mutating API routes with lookup/store logic and change task transitions to desired-state semantics.
- [ ] Add a failing Swift test proving `PendingWrite.id` is sent as the idempotency key.
- [ ] Thread the write ID through `ArgonClient.apply` and set the header.
- [ ] Run Python module tests and Swift package tests.
- [ ] Commit the retry-safety repair.

### Task 5: Correct Classroom truth, dispositions, and due dates

**Files:**
- Modify: `argon/integrations/google.py`
- Modify: `argon/runtime.py`
- Modify: `argon/store.py` only if source-date metadata is required.
- Modify: `argon/api.py`
- Test: module self-tests in the same Python modules.

**Interfaces:**
- Consumes: Classroom submission states and coursework records.
- Produces: a sync result containing `display_items`, `outstanding_ids`, and `refused_courses`; `classroom_task_date(coursework) -> str` implementing timed-instant versus date-only-work-night behavior.

- [ ] Add a failing Google self-test proving an outstanding item older than the display floor remains in `outstanding_ids` while absent from `display_items`.
- [ ] Add failing date tests: `06:59Z` converts to the prior Pacific date, `15:30Z` stays on its local date/time, and date-only `2026-09-22` maps to work date `2026-09-21`.
- [ ] Refactor the fetch once so complete outstanding IDs and bounded display records come from the same per-course reads.
- [ ] Update runtime sync to close only IDs absent from complete outstanding truth and never on partial course reads.
- [ ] Add a failing API test proving phone completion writes a `done` Classroom disposition.
- [ ] Route phone completion through the same disposition behavior as agent completion.
- [ ] Run all Python module self-tests.
- [ ] Commit the Classroom/due-date repair.

### Task 6: Changelog, full verification, and deployment

**Files:**
- Create: `CHANGELOG.md`
- Verify only: all modified source and tests.

**Interfaces:**
- Consumes: completed repairs from Tasks 1–5.
- Produces: documented release and verified deployed server revision.

- [ ] Add a dated changelog entry covering registration recovery, truthful locks, task/focus retry safety, Classroom closure semantics, dispositions, and work-night dates.
- [ ] Run `git diff --check`.
- [ ] Run `./check.sh` and require Python checks, Swift tests, and the full iOS app build to pass.
- [ ] Inspect `git diff` against this plan and confirm every spec requirement is represented.
- [ ] Commit the changelog and any verification-only adjustments.
- [ ] Push the branch and update the server checkout only after local verification passes; restart `argonv2` and verify its commit, health, doctor output, logs, Classroom sync, and API state.
- [ ] Report the remaining device step explicitly: install/open the new build, then verify `device` is registered and the phone reports the current lock version.
