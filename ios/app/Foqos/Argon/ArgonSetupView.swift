import FamilyControls
import SwiftData
import SwiftUI

/// First run: pick the apps Argon may block, and decide about weekends.
///
/// The old flow asked him to go into Foqos, create a blocked profile, and name
/// it exactly "Argon Lockdown" — a string match between two screens that never
/// mention each other. Getting it wrong, or renaming it later, meant every
/// lock silently did nothing. The profile still exists underneath, because all
/// of Foqos's session, shield and break machinery is built on it; it is just
/// created from this picker instead of by hand, and kept in step from here.
struct ArgonSetupView: View {
  @Environment(\.modelContext) private var context
  @Environment(\.dismiss) private var dismiss

  @State private var selection = FamilyActivitySelection()
  @State private var picking = false
  @State private var weekend = false
  @State private var weekendMinutes = 15
  @State private var saving = false
  @State private var error: String?

  /// Set once he finishes. `ArgonRootView` shows this sheet until it is true,
  /// so a half-finished setup is offered again rather than silently skipped.
  @AppStorage("argon.setupComplete") private var setupComplete = false

  var body: some View {
    NavigationStack {
      ScrollView {
        VStack(spacing: 16) {
          header
          appsCard
          weekendCard
          if let error {
            Text(error).font(Argon.detail).foregroundStyle(Argon.overdue)
              .fixedSize(horizontal: false, vertical: true)
          }
          saveButton
        }
        .padding(.horizontal, 16)
        .padding(.bottom, 32)
      }
      .scrollContentBackground(.hidden)
      .argonAmbience()
      .navigationTitle("Set up Argon")
      .navigationBarTitleDisplayMode(.large)
      .toolbarBackground(.hidden, for: .navigationBar)
    }
    .tint(Argon.accent)
    .familyActivityPicker(isPresented: $picking, selection: $selection)
  }

  // MARK: - cards

  private var header: some View {
    ArgonGlass {
      Text("Argon blocks these when you ask it to, and when you've agreed to a "
           + "lock-in. Nothing is blocked until then.")
        .font(Argon.body)
        .foregroundStyle(Argon.Tone.secondary)
        .fixedSize(horizontal: false, vertical: true)
    }
  }

  private var appsCard: some View {
    ArgonGlass {
      VStack(alignment: .leading, spacing: 12) {
        Text("APPS TO BLOCK")
          .font(Argon.label.weight(.semibold)).tracking(1.2)
          .foregroundStyle(Argon.accentSoft)

        Text(countLabel)
          .font(Argon.body).foregroundStyle(Argon.Tone.primary)

        Button {
          picking = true
        } label: {
          HStack(spacing: 8) {
            Image(systemName: "square.grid.2x2")
            Text(chosenAny ? "Change selection" : "Choose apps")
          }
          .font(Argon.body.weight(.semibold))
          .foregroundStyle(.white)
          .frame(maxWidth: .infinity, minHeight: 44)
          .background {
            Capsule().fill(LinearGradient(
              colors: [Argon.accent, Argon.accentDeep],
              startPoint: .topLeading, endPoint: .bottomTrailing))
          }
        }
        .buttonStyle(.plain)
      }
    }
  }

  private var weekendCard: some View {
    ArgonGlass {
      VStack(alignment: .leading, spacing: 12) {
        Toggle(isOn: $weekend) {
          VStack(alignment: .leading, spacing: 3) {
            Text("Weekend mode").font(Argon.body).foregroundStyle(Argon.Tone.primary)
            Text("Meter these apps instead of blocking them outright — a budget "
                 + "each hour, then the shield.")
              .font(Argon.label).foregroundStyle(Argon.Tone.faint)
              .fixedSize(horizontal: false, vertical: true)
          }
        }
        .tint(Argon.accent)

        if weekend {
          ArgonDivider()
          HStack {
            Text("Budget an hour").font(Argon.body)
              .foregroundStyle(Argon.Tone.primary)
            Spacer()
            Picker("", selection: $weekendMinutes) {
              ForEach([5, 10, 15, 20, 30, 45, 60], id: \.self) { Text("\($0)m").tag($0) }
            }
            .pickerStyle(.menu)
            .tint(Argon.accentSoft)
          }
        }
      }
    }
  }

  private var saveButton: some View {
    Button {
      Task { await save() }
    } label: {
      HStack(spacing: 8) {
        if saving { ProgressView().controlSize(.small).tint(.white) }
        Text(saving ? "Saving…" : "Done")
      }
      .font(Argon.body.weight(.semibold))
      .foregroundStyle(.white)
      .frame(maxWidth: .infinity, minHeight: 50)
      .background {
        Capsule().fill(chosenAny
          ? AnyShapeStyle(LinearGradient(colors: [Argon.accent, Argon.accentDeep],
                                         startPoint: .topLeading,
                                         endPoint: .bottomTrailing))
          : AnyShapeStyle(Color.white.opacity(0.10)))
      }
    }
    .buttonStyle(.plain)
    .disabled(!chosenAny || saving)
  }

  // MARK: - state

  private var chosenAny: Bool {
    !selection.applicationTokens.isEmpty
      || !selection.categoryTokens.isEmpty
      || !selection.webDomainTokens.isEmpty
  }

  private var countLabel: String {
    guard chosenAny else { return "Nothing chosen yet." }
    var parts: [String] = []
    let apps = selection.applicationTokens.count
    let cats = selection.categoryTokens.count
    let webs = selection.webDomainTokens.count
    if apps > 0 { parts.append("\(apps) app\(apps == 1 ? "" : "s")") }
    if cats > 0 { parts.append("\(cats) categor\(cats == 1 ? "y" : "ies")") }
    if webs > 0 { parts.append("\(webs) site\(webs == 1 ? "" : "s")") }
    return parts.joined(separator: ", ")
  }

  /// Write the selection into the profile Argon locks, creating it if needed.
  ///
  /// Reusing the existing row rather than adding another every time he opens
  /// this: a second "Argon" profile would leave the reconciler picking one of
  /// two by name, and locks would apply to whichever it happened to find.
  private func save() async {
    saving = true
    defer { saving = false }
    do {
      let all = try BlockedProfiles.fetchProfiles(in: context)
      let name = ArgonBridge.shared.profileName
      if let existing = all.first(where: { $0.name == name }) {
        try BlockedProfiles.updateProfile(existing, in: context, selection: selection)
      } else {
        _ = try BlockedProfiles.createProfile(
          in: context, name: name, selection: selection,
          blockingStrategyId: ManualBlockingStrategy.id)
      }
      SharedData.setArgonMeteredSelection(weekend ? selection : nil)
      await ArgonBridge.shared.setFocusMode(
        weekend ? "weekend" : "normal",
        allowanceMinutes: weekend ? weekendMinutes : nil,
        perHours: weekend ? 1 : nil)
      setupComplete = true
      dismiss()
    } catch {
      self.error = "Couldn't save that: \(error.localizedDescription)"
    }
  }
}
