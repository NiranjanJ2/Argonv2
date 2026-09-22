# Reliable Sync and Locking Design

## Goal

Make Argon v2 reliably carry Classroom work through the server and database,
keep phone task state converged under retries, and apply or release lockdowns
truthfully even when the app or network is unreliable.

## User-visible truth

- A lock is never described as armed or applied unless iOS accepted the
  Device Activity schedule or the shield is actually active.
- Starting, stopping, and completing a task are desired-state operations.
  Repeating one after a lost response succeeds instead of producing a false
  conflict.
- An outbox retry cannot duplicate a new task or a chat message.
- Classroom assignments disappear from the board only after Classroom says
  they are no longer outstanding or Niranjan explicitly completes/ignores them.
- The task date is the work night. Timed Classroom deadlines retain their exact
  local instant; date-only Classroom homework is shown on the previous local
  day, because Japanese, AI, AP Lang, and math work is expected the night before
  Classroom's submission date. This rule is based on deadline shape, not hard-
  coded course names.

## Linear repair sequence

### 1. Runnable sync tests

Add `ArgonLog.swift` to the Foundation-only Swift package target so the existing
phone cache/outbox tests compile again. Keep iOS-only files excluded.

### 2. One app delegate and self-healing device registration

The SwiftUI-created `ArgonAppDelegate` becomes the sole instance. Code that
currently reaches `ArgonAppDelegate.shared` receives or resolves that installed
instance instead of constructing a second client and store.

At every launch, the app registers with APNs when notification authorization is
already granted and retries uploading any stored token. A successful upload is
cached together with a server identity derived from base URL and API token; a
server/config change invalidates the cached acknowledgement. This prevents a
phone from believing a token is synced merely because it once reached v1 or an
older v2 state directory.

### 3. Truthful lock convergence

`ArgonLockWindow.arm` returns success or a concrete error. It rejects an empty
blocking selection before scheduling. `ArgonLockReconciler` reports that result
to the server and only acknowledges a future lock when the schedule was
accepted. Immediate locks continue to report the actual shield state. The
server retains desired and applied versions separately.

### 4. Retry-safe task and chat writes

Each queued write carries its stable UUID to the server as an idempotency key.
The server stores completed keys and their response in SQLite, atomically with
the relevant mutation where practical. Replaying a completed request returns
the prior successful response.

Start, stop, complete, and read operations additionally use desired-state
semantics: already started/stopped/completed/read is success. This makes them
safe for old clients and protects against a response being lost after commit.

### 5. Complete Classroom truth versus display window

The Google integration returns both:

- every outstanding assignment ID successfully read from Classroom; and
- the bounded, dated assignments eligible for display.

The runtime upserts displayable assignments but closes an existing task only
when its ID is absent from the complete outstanding-ID set. It never infers
completion from the 14-day/30-day display window or from a missing due date.
Phone completion records the same durable Classroom disposition as agent-side
completion.

Timed deadlines are converted from Google's UTC `dueDate` + `dueTime` pair and
retain the resulting local date and time. Date-only homework uses the previous
local calendar day as its task/work date while retaining the original Classroom
date as source metadata if needed for diagnostics.

## Testing

Each stage begins with a regression test that fails for the observed problem.
Python module self-tests cover API idempotency, Classroom closure, dispositions,
and due-date mapping. Swift package tests cover registration state, outbox
retries, and pure lock scheduling decisions; the complete iOS application build
type-checks Device Activity and app-delegate integration. Final verification is
`./check.sh`, followed by live server deployment checks. Device behavior is not
claimed verified until a new phone build registers a token and reports the
current desired lock version.

## Changelog

Add a root `CHANGELOG.md` entry describing the repaired phone registration,
truthful lock acknowledgements, retry-safe writes, Classroom reconciliation,
and work-night due-date rule.
