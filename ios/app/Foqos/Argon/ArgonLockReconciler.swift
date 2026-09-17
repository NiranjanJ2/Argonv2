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
    let wanted = (lock?.isLive ?? false)
    // loadActiveSession, not the published property: on a background wake the
    // published value has not necessarily been populated, and reconciling
    // against a stale nil starts a second session on top of a live one.
    StrategyManager.shared.loadActiveSession(context: context)
    let active = StrategyManager.shared.activeSession

    if wanted, active == nil {
      guard let profile = profile(in: context) else { return }
      StrategyManager.shared.startSessionFromBackground(
        profile.id, context: context,
        durationInMinutes: max(1, (lock?.secondsLeft ?? 0) / 60))
      UserDefaults.standard.set(profile.id.uuidString, forKey: ownedKey)
      return
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

  /// Marks the session as one Argon started, so releasing it later is allowed.
  private static let ownedKey = "argon.lockOwnedProfileID"

  /// The profile named in Settings, or the most recent one.
  ///
  /// Falling back rather than doing nothing: a name that no longer matches a
  /// profile — renamed, deleted — would otherwise make every lock silently do
  /// nothing, and a lock that quietly does nothing is worse than no feature.
  private static func profile(in context: ModelContext) -> BlockedProfiles? {
    let wanted = ArgonBridge.shared.profileName
    let all = (try? BlockedProfiles.fetchProfiles(in: context)) ?? []
    return all.first { $0.name == wanted }
      ?? (try? BlockedProfiles.fetchMostRecentlyUpdatedProfile(in: context)) ?? all.first
  }
}
