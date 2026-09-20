import FamilyControls
import SwiftUI

/// Weekend mode, where the blocking lives.
///
/// It used to be a section in the stock settings screen — three taps away from
/// the place he is actually standing when he wants it, and filed with the
/// server address and the API token, which are not decisions he makes weekly.
/// It belongs next to the rest of the blocking controls.
///
/// Metering rather than blocking: apps stay open until the hour's budget is
/// spent, then they shield until the next one. Time he does not spend is not
/// carried over, because a bankable balance turns the whole thing into a game
/// about saving minutes.
struct ArgonWeekendCard: View {
  @ObservedObject private var bridge = ArgonBridge.shared
  @AppStorage("argon.weekendMinutes") private var minutes = 15
  @State private var picking = false
  @State private var selection = FamilyActivitySelection()
  @State private var busy = false
  @State private var error: String?

  private var isOn: Bool { bridge.desiredMode == "weekend" }

  var body: some View {
    ArgonGlass(tint: isOn ? Argon.accent : Argon.accentDeep) {
      VStack(alignment: .leading, spacing: 12) {
        Toggle(isOn: Binding(get: { isOn },
                             set: { on in set(on ? "weekend" : "off") })) {
          VStack(alignment: .leading, spacing: 3) {
            Text("Weekend mode")
              .font(Argon.heading).foregroundStyle(Argon.Tone.primary)
            Text(isOn ? "\(minutes) minutes an hour, then shielded"
                      : "Meter these apps instead of blocking them")
              .font(Argon.label).foregroundStyle(Argon.Tone.faint)
              .fixedSize(horizontal: false, vertical: true)
          }
        }
        .tint(Argon.accent)
        .disabled(busy)

        if let error {
          Label(error, systemImage: "exclamationmark.triangle.fill")
            .font(Argon.label).foregroundStyle(Argon.overdue)
            .fixedSize(horizontal: false, vertical: true)
        }

        if isOn {
          ArgonDivider()
          HStack {
            Text("Budget an hour")
              .font(Argon.body).foregroundStyle(Argon.Tone.primary)
            Spacer()
            Picker("", selection: $minutes) {
              ForEach([5, 10, 15, 20, 30, 45, 60], id: \.self) { Text("\($0)m").tag($0) }
            }
            .pickerStyle(.menu)
            .tint(Argon.accentSoft)
            .onChange(of: minutes) { _, _ in if isOn { set("weekend") } }
          }

          ArgonDivider()
          Button {
            selection = SharedData.argonMeteredSelection()
              ?? ArgonMetered.configuredApps ?? FamilyActivitySelection()
            picking = true
          } label: {
            HStack {
              Text("Apps to meter")
                .font(Argon.body).foregroundStyle(Argon.Tone.primary)
              Spacer()
              Text(caption)
                .font(Argon.label).foregroundStyle(Argon.Tone.faint)
              Image(systemName: "chevron.right")
                .font(.caption).foregroundStyle(Argon.Tone.faint)
            }
            .frame(minHeight: 44)
            .contentShape(Rectangle())
          }
          .buttonStyle(.plain)
        }
      }
    }
    .onAppear {
      // The switch and the shield can disagree: an iOS update, a reinstall or
      // a revoked Screen Time authorisation stops monitoring without touching
      // the stored mode, and the card would then read "on" over nothing. If it
      // claims to be on, make it true.
      if isOn && !ArgonMetered.isMonitoring {
        ArgonLog.note("weekend", "was on but not monitoring — reasserting")
        set("weekend")
      }
    }
    .familyActivityPicker(isPresented: $picking, selection: $selection)
    .onChange(of: picking) { _, open in
      // Written when the sheet closes, not on every keystroke of selection:
      // the picker mutates `selection` continuously while he is choosing.
      if !open { SharedData.setArgonMeteredSelection(selection) }
    }
  }

  private var caption: String {
    guard let chosen = SharedData.argonMeteredSelection() ?? ArgonMetered.configuredApps
    else {
      // Says what it will actually do. "None" would read as "nothing is
      // metered" when in fact the whole blocked set is.
      return "Same as \(bridge.profileName)"
    }
    let n = FamilyActivityUtil.countSelectedActivities(chosen)
    return n == 0 ? "Same as \(bridge.profileName)" : "\(n) selected"
  }

  /// Apply it here, then tell the server.
  ///
  /// The toggle used to only call setFocusMode, which POSTs the mode and
  /// nothing more — the server records it as an observation and takes no
  /// action, because blocking happens on the device. applyArgonMeteredMode
  /// existed the whole time and had no caller anywhere in the app, so the
  /// switch moved, the server learned about it, and not one app was ever
  /// metered.
  ///
  /// On-device first and reported second, so a server that is unreachable does
  /// not stop the shield he just asked for.
  private func set(_ mode: String) {
    busy = true
    let wanted = mode == "weekend"
    do {
      if wanted {
        try StrategyManager.shared.applyArgonMeteredMode(
          profileName: bridge.profileName,
          minutes: minutes,
          perHours: 1,
          context: container.mainContext)
      } else {
        StrategyManager.shared.applyArgonUnlock(context: container.mainContext)
      }
      error = nil
      ArgonLog.note("weekend", "\(wanted ? "on" : "off") — \(minutes)m/hour applied")
    } catch {
      // Say so rather than leaving the switch looking on. The commonest cause
      // is no profile yet, which setup creates — and which he can only fix if
      // he is told.
      self.error = error.localizedDescription
      ArgonLog.note("weekend", "failed: \(error.localizedDescription)")
      busy = false
      return
    }
    Task {
      await bridge.setFocusMode(mode,
                                allowanceMinutes: wanted ? minutes : nil,
                                perHours: wanted ? 1 : nil)
      busy = false
    }
  }
}
