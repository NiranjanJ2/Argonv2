import FamilyControls
import Foundation
import SwiftData

/// Applies the lock the server published, on whatever wake the app next gets.
///
/// ## Why reconcile instead of "the agent blocks the phone"
///
/// The server cannot reach into the phone. It publishes desired state; the app
/// makes reality match on its next wake — a silent push, a `BGAppRefreshTask`,
/// or the app coming to the foreground. v1 drove this from a `Timer` inside the
/// app, which iOS suspends the moment the app backgrounds, so an agent-ordered
/// block only ever applied while he happened to be looking at the screen. That
/// is the bug this file exists to close.
///
/// ## What iOS actually guarantees
///
/// Not much, and this is worth being honest about rather than discovering
/// later. Silent pushes are rate-limited and may be delayed or dropped; a
/// `BGAppRefreshTask` is opportunistic and the system decides when it runs.
/// So an agent-ordered lock is **best-effort and may land late**. The only
/// mechanism that applies with hard timing and the app fully closed is a
/// `DeviceActivitySchedule` evaluated by the system, which is scheduled ahead
/// of time rather than ordered on the spot. A standing evening block belongs
/// there; "block me now because I asked" belongs here.
///
/// The agent's tool description says the same thing, so it does not promise
/// him a lock is on when it is only published.
@MainActor
enum ArgonLockReconciler {

  /// Make the phone match `lock`. Safe to call on every refresh: it compares
  /// before it acts, so a wake with nothing to do costs a fetch and no writes.
  static func reconcile(_ lock: ArgonLock?, context: ModelContext) {
    // The emergency release is checked first and locally. v1's rule: an escape
    // hatch that needs a network is not an escape hatch. While it is engaged
    // nothing is applied, whatever the server wants, and the refusal is
    // reported so Argon knows the block did not land rather than assuming it
    // did.
    if let until = ArgonOverride.activeUntil, lock != nil {
      ArgonLockWindow.disarm()
      release(context: context)
      report(lock, shielded: false,
             error: "override until \(until.formatted(date: .omitted, time: .shortened))")
      return
    }

    // Arm the whole window with the system first, so the start and the end
    // happen on time even if this app never runs again between now and then.
    // Reconciling the Foqos session below is what makes the *current* state
    // right; the schedule is what makes the boundaries right.
    if let lock, lock.secondsLeft > 0,
       let start = lock.startsAt, let end = lock.endsAt {
      let arm = ArgonLockWindow.arm(from: start, until: end,
                                    selection: lockSelection(in: context))
      // Report the arming, not just the blocking. A lock booked for 20:30 is
      // *handled* the moment the window is armed; without saying so the server
      // reads "published, never confirmed" for an hour and Argon cannot tell
      // him it is set up.
      if !lock.isLive {
        report(lock, shielded: false, error: arm.error)
        return
      }
      // A live lock can survive a short-window scheduling rejection because
      // the Foqos session below applies immediately. An empty profile cannot
      // block anything by either route, so do not claim it did.
      if !ArgonLockWindow.canApplyImmediately(after: arm) {
        report(lock, shielded: false, error: arm.error)
        return
      }
    } else {
      ArgonLockWindow.disarm()
    }

    let wanted = (lock?.isLive ?? false)
    // loadActiveSession, not the published property: on a background wake the
    // published value has not necessarily been populated, and reconciling
    // against a stale nil starts a second session on top of a live one.
    StrategyManager.shared.loadActiveSession(context: context)
    let active = StrategyManager.shared.activeSession

    if wanted, active == nil {
      guard let profile = profile(in: context) else {
        // Nothing to block with. Said out loud rather than swallowed: the
        // commonest cause is setup never finished, and he can only fix what he
        // is told about.
        report(lock, shielded: false, error: "no blocking profile is set up")
        return
      }
      StrategyManager.shared.startSessionFromBackground(
        profile.id, context: context,
        durationInMinutes: max(1, (lock?.secondsLeft ?? 0) / 60))
      UserDefaults.standard.set(profile.id.uuidString, forKey: ownedKey)
      report(lock, shielded: true, error: nil)
      return
    }

    if wanted, active != nil {
      report(lock, shielded: true, error: nil)
    }

    if !wanted, let active {
      // Only release what Argon started. He starts sessions himself with NFC
      // and QR all day; a lock lapsing must not tear down a session he chose,
      // which is the obvious version of this and is wrong.
      guard let owned = UserDefaults.standard.string(forKey: ownedKey),
            owned == active.blockedProfile.id.uuidString else { return }
      StrategyManager.shared.stopSessionFromBackground(
        active.blockedProfile.id, context: context)
      UserDefaults.standard.removeObject(forKey: ownedKey)
    }
  }

  /// Tell the server what actually happened, success or failure.
  ///
  /// Keyed on the lock's version, so the server compares what it wanted with
  /// what landed instead of guessing from silence.
  private static func report(_ lock: ArgonLock?, shielded: Bool, error: String?) {
    guard let lock else { return }
    ArgonLog.note("lock", "v\(lock.version) shielded=\(shielded)"
                  + (error.map { " — \($0)" } ?? ""))
    Task { try? await ArgonAppDelegate.shared.client.reportLock(
      version: lock.version, shielded: shielded, error: error) }
  }

  /// Drop whatever Argon raised, leaving anything he started himself alone.
  private static func release(context: ModelContext) {
    StrategyManager.shared.loadActiveSession(context: context)
    guard let active = StrategyManager.shared.activeSession,
          let owned = UserDefaults.standard.string(forKey: ownedKey),
          owned == active.blockedProfile.id.uuidString else { return }
    StrategyManager.shared.stopSessionFromBackground(
      active.blockedProfile.id, context: context)
    UserDefaults.standard.removeObject(forKey: ownedKey)
  }

  /// Marks the session as one Argon started, so releasing it later is allowed.
  private static let ownedKey = "argon.lockOwnedProfileID"

  /// What a lock shields: everything the lockdown profile blocks.
  ///
  /// Not the weekend list — that is the set he rations, and a lock-in is meant
  /// to be the whole thing.
  private static func lockSelection(in context: ModelContext) -> FamilyActivitySelection {
    profile(in: context)?.selectedActivity ?? FamilyActivitySelection()
  }

  /// The profile named in Settings, or the most recent one.
  ///
  /// Falling back rather than doing nothing: a name that no longer matches a
  /// profile — renamed, deleted — would otherwise make every lock silently do
  /// nothing, and a lock that quietly does nothing is worse than no feature.
  ///
  /// Also the profile the evening routine blocks with (ArgonAppDelegate), so
  /// a lock and the routine can never disagree about which apps go dark.
  static func profile(in context: ModelContext) -> BlockedProfiles? {
    let wanted = ArgonBridge.shared.profileName
    let all = (try? BlockedProfiles.fetchProfiles(in: context)) ?? []
    return all.first { $0.name == wanted }
      ?? (try? BlockedProfiles.fetchMostRecentlyUpdatedProfile(in: context)) ?? all.first
  }
}
