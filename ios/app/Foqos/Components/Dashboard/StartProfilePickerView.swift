import SwiftUI
import UIKit

struct StartProfilePickerView: View {
  @Environment(\.dismiss) private var dismiss
  @EnvironmentObject private var themeManager: ThemeManager

  let profiles: [BlockedProfiles]
  let isBlocking: Bool
  let activeSessionProfileId: UUID?
  let startingProfileId: UUID?
  let onGoTapped: (BlockedProfiles) -> Void

  @State private var selectedProfileId: UUID?

  init(
    profiles: [BlockedProfiles],
    isBlocking: Bool,
    activeSessionProfileId: UUID?,
    startingProfileId: UUID? = nil,
    onGoTapped: @escaping (BlockedProfiles) -> Void
  ) {
    self.profiles = profiles
    self.isBlocking = isBlocking
    self.activeSessionProfileId = activeSessionProfileId
    self.startingProfileId = startingProfileId
    self.onGoTapped = onGoTapped
    _selectedProfileId = State(
      initialValue: profiles.first(where: { $0.id == startingProfileId })?.id ?? profiles.first?.id)
  }

  private var selectedProfile: BlockedProfiles? {
    guard let selectedProfileId else { return nil }
    return profiles.first(where: { $0.id == selectedProfileId })
  }

  private var canGo: Bool {
    selectedProfile != nil && !isBlocking
  }

  var body: some View {
    NavigationStack {
      VStack(spacing: 0) {
        if profiles.isEmpty {
          EmptyView(
            iconName: "person.crop.circle.badge.plus",
            headingText: "Create a profile before starting a focus session"
          )
          .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
          ScrollView {
            VStack(spacing: Argon.overshoot * 2) {
              DraftSectionLabel(title: "Start a profile")
                .frame(maxWidth: .infinity, alignment: .leading)

              if isBlocking {
                activeSessionNotice
              }

              ForEach(profiles) { profile in
                StartProfilePickerRow(
                  profile: profile,
                  isSelected: profile.id == selectedProfileId,
                  isActive: profile.id == activeSessionProfileId,
                  onTap: {
                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                    withAnimation(.spring(response: 0.28, dampingFraction: 0.74)) {
                      selectedProfileId = profile.id
                    }
                  }
                )
              }
            }
            .padding(.horizontal, Argon.margin)
            .padding(.vertical, 12)
          }
        }

        goButton
      }
      .padding(.top, 18)
      .argonAmbience()

      .onChange(of: profiles) { _, newProfiles in
        if selectedProfile == nil {
          withAnimation(.spring(response: 0.28, dampingFraction: 0.74)) {
            selectedProfileId = newProfiles.first?.id
          }
        }
      }
      .onChange(of: startingProfileId) { _, newValue in
        if let newValue, profiles.contains(where: { $0.id == newValue }) {
          withAnimation(.spring(response: 0.28, dampingFraction: 0.74)) {
            selectedProfileId = newValue
          }
        }
      }
    }
  }

  private var activeSessionNotice: some View {
    HStack(spacing: 10) {
      Image(systemName: "lock")
        .foregroundStyle(Argon.accent)

      Text("A profile is already active. Stop it before starting another one.")
        .font(Argon.detail)
        .foregroundStyle(Argon.Tone.secondary)
        .fixedSize(horizontal: false, vertical: true)

      Spacer(minLength: 0)
    }
    .padding(14)
    .draftBox(stroke: Argon.accent.opacity(0.55), fill: Argon.accent.opacity(0.06))
  }

  private var goButton: some View {
    VStack(spacing: 8) {
      ShimmerLauncherButton(
        title: "Go",
        height: 56,
        isEnabled: canGo,
        accessibilityLabel: "Start selected profile",
        action: goTapped
      )
    }
    .padding(.horizontal, Argon.margin)
    .padding(.top, 10)
    .padding(.bottom, 16)
  }

  private func goTapped() {
    guard let selectedProfile, !isBlocking else { return }
    UIImpactFeedbackGenerator(style: .medium).impactOccurred()
    onGoTapped(selectedProfile)
    dismiss()
  }
}

private struct PickerRowButtonStyle: ButtonStyle {
  let isSelected: Bool

  func makeBody(configuration: Configuration) -> some View {
    configuration.label
      .opacity(configuration.isPressed ? 0.7 : 1)
  }
}

private struct StartProfilePickerRow: View {
  @EnvironmentObject private var themeManager: ThemeManager

  let profile: BlockedProfiles
  let isSelected: Bool
  let isActive: Bool
  let onTap: () -> Void

  var body: some View {
    Button(action: onTap) {
      ProfileSummaryRow(
        profile: profile,
        isActive: isActive,
        metadata: .appsAndDomains,
        showsStatusLine: true
      ) {
        DraftCheck(on: isSelected)
      }
      .padding(14)
      .draftBox(stroke: isSelected ? Argon.accent.opacity(0.7) : Argon.line,
                fill: isSelected ? Argon.accent.opacity(0.08) : .clear)
      .contentShape(Rectangle())
    }
    .buttonStyle(PickerRowButtonStyle(isSelected: isSelected))
    .animation(.spring(response: 0.28, dampingFraction: 0.78), value: isSelected)
  }
}

#Preview {
  StartProfilePickerView(
    profiles: [
      BlockedProfiles(name: "Work"),
      BlockedProfiles(name: "Study"),
    ],
    isBlocking: false,
    activeSessionProfileId: nil,
    onGoTapped: { _ in }
  )
  .environmentObject(ThemeManager.shared)
}
