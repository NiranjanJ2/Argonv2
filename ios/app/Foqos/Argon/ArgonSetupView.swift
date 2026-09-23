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
    ScrollView {
      VStack(alignment: .leading, spacing: 8) {
        header
        DraftSectionLabel(title: "Apps to block")
          .padding(.horizontal, Argon.overshoot)
        appsCard
        DraftSectionLabel(title: "Weekends")
          .padding(.horizontal, Argon.overshoot).padding(.top, 10)
        weekendCard
        if let error {
          Text(error).font(Argon.detail)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(12)
            .draftAlarm()
            .padding(Argon.overshoot)
        }
        saveButton
          // The style pads 6 for its own overshoot; boxes pad `overshoot`.
          .padding(.horizontal, Argon.overshoot - 6).padding(.top, 10)
      }
      // Box sides sit at `margin`; each box's overshoot lives in the gap.
      .padding(.horizontal, Argon.margin - Argon.overshoot)
      .padding(.bottom, 32)
    }
    .scrollIndicators(.hidden)
    .argonAmbience()
    .tint(Argon.accent)
    .preferredColorScheme(.dark)
    .familyActivityPicker(isPresented: $picking, selection: $selection)
  }

  // MARK: - cards

  private var header: some View {
    VStack(alignment: .leading, spacing: 8) {
      Text("Set up Argon")
        .font(Argon.screenTitle)
        .foregroundStyle(Argon.Tone.primary)
      Text("Argon blocks these when you ask it to, and when you've agreed to a "
           + "lock-in. Nothing is blocked until then.")
        .font(Argon.body)
        .foregroundStyle(Argon.Tone.secondary)
        .fixedSize(horizontal: false, vertical: true)
    }
    .padding(.horizontal, Argon.overshoot)
    .padding(.top, 32).padding(.bottom, 14)
  }

  private var appsCard: some View {
    ArgonGlass {
      VStack(alignment: .leading, spacing: 12) {
        Text(countLabel)
          .font(Argon.body)
          .foregroundStyle(chosenAny ? Argon.Tone.primary : Argon.Tone.faint)

        Button {
          picking = true
        } label: {
          Label(chosenAny ? "Change selection" : "Choose apps",
                systemImage: "square.grid.2x2")
        }
        .buttonStyle(ArgonButtonStyle(prominent: false))
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
            Picker("Budget an hour", selection: $weekendMinutes) {
              ForEach([5, 10, 15, 20, 30, 45, 60], id: \.self) { Text("\($0)m").tag($0) }
            }
            .labelsHidden()
            .pickerStyle(.menu)
            .tint(Argon.accent)
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
        if saving { ProgressView().controlSize(.small).tint(Argon.accent) }
        Text(saving ? "Saving…" : "Done")
      }
    }
    .buttonStyle(ArgonButtonStyle())
    .disabled(!chosenAny || saving)
    .opacity(!chosenAny || saving ? 0.4 : 1)
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
