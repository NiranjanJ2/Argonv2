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

  private func set(_ mode: String) {
    busy = true
    Task {
      await bridge.setFocusMode(mode,
                                allowanceMinutes: mode == "weekend" ? minutes : nil,
                                perHours: mode == "weekend" ? 1 : nil)
      busy = false
    }
  }
}
