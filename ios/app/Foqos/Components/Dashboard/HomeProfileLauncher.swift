import SwiftUI
import UIKit

struct HomeProfileLauncher: View {
  @EnvironmentObject private var themeManager: ThemeManager

  let activeProfile: BlockedProfiles?
  let displayTime: TimeInterval
  var isBreakActive = false
  var isPauseActive = false
  let onStartTapped: () -> Void
  var onActiveTapped: () -> Void = {}

  private let inactiveButtonHeight: CGFloat = 56
  private let activeButtonHeight: CGFloat = 80

  var body: some View {
    Group {
      if let activeProfile {
        activeProfileButton(activeProfile)
      } else {
        inactiveLauncherButtons
      }
    }
    // The box's lines sit on the same margin as every box above it; the
    // overshoot lives in the gap.
    .padding(.horizontal, Argon.margin)
    .padding(.top, 10)
    .padding(.bottom, 8)
    // Its own ground, so list content scrolling underneath does not show
    // through the gap between the launcher and the tab bar.
    .background(Argon.Ink.base)
    .overlay(alignment: .top) { ArgonDivider() }
  }

  private var inactiveLauncherButtons: some View {
    ShimmerLauncherButton(
      title: "Begin focus",
      iconName: "play.fill",
      height: inactiveButtonHeight,
      accessibilityLabel: "Start Profile",
      action: startTapped
    )
  }

  private func activeProfileButton(_ profile: BlockedProfiles) -> some View {
    Button(action: activeTapped) {
      ProfileSummaryRow(
        profile: profile,
        isActive: true,
        metadata: .appsAndDomains,
        showsStatusLine: true,
        layout: .compact
      ) {
        activeAccessory
      }
      .padding(.horizontal, 16)
      .frame(maxWidth: .infinity)
      .frame(height: activeButtonHeight)
      .background(CardBackground(isActive: true))
      .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
    .foregroundStyle(Argon.Tone.primary)
    .accessibilityLabel(activeAccessibilityLabel(for: profile))
  }

  @ViewBuilder
  private var activeAccessory: some View {
    if let activeStateTitle, let activeStateImageName {
      HStack(spacing: 6) {
        Image(systemName: activeStateImageName)
          .font(.callout.weight(.semibold))

        Text(activeStateTitle)
          .font(Argon.detail.weight(.semibold))
          .lineLimit(1)
          .minimumScaleFactor(0.72)
      }
      .foregroundStyle(Argon.accent)
    } else {
      Text(DateFormatters.formatDurationClock(displayTime))
        .font(.title3.weight(.semibold).monospacedDigit().width(.expanded))
        .foregroundStyle(Argon.accent)
        .lineLimit(1)
        .minimumScaleFactor(0.72)
        .contentTransition(.numericText())
        .animation(.default, value: displayTime)
    }
  }

  private var activeStateTitle: String? {
    if isPauseActive {
      return "Paused"
    }
    if isBreakActive {
      return "On Break"
    }
    return nil
  }

  private var activeStateImageName: String? {
    // SF Symbols, not the colour stickers: they take the sheet's blue.
    if isPauseActive {
      return "pause"
    }
    if isBreakActive {
      return "cup.and.saucer"
    }
    return nil
  }

  private func startTapped() {
    UIImpactFeedbackGenerator(style: .light).impactOccurred()
    onStartTapped()
  }

  private func activeTapped() {
    UIImpactFeedbackGenerator(style: .light).impactOccurred()
    onActiveTapped()
  }

  private func activeAccessibilityLabel(for profile: BlockedProfiles) -> String {
    if let activeStateTitle {
      return "\(activeStateTitle) Profile \(profile.name)"
    }
    return "Active Profile \(profile.name)"
  }
}

#Preview("Inactive") {
  VStack {
    Spacer()
    HomeProfileLauncher(
      activeProfile: nil,
      displayTime: 0,
      onStartTapped: {}
    )
  }
  .argonAmbience()
  .environmentObject(ThemeManager.shared)
}

#Preview("Active") {
  VStack {
    Spacer()
    HomeProfileLauncher(
      activeProfile: BlockedProfiles(
        name: "Work Focus",
        blockingStrategyId: ManualBlockingStrategy.id,
        enableLiveActivity: true,
        reminderTimeInSeconds: 3600,
        enableBreaks: true,
        domains: ["example.com", "social.example"]
      ),
      displayTime: 3665,
      onStartTapped: {}
    )
  }
  .argonAmbience()
  .environmentObject(ThemeManager.shared)
}

#Preview("Paused") {
  VStack {
    Spacer()
    HomeProfileLauncher(
      activeProfile: BlockedProfiles(
        name: "Work Focus",
        blockingStrategyId: NFCPauseTimerBlockingStrategy.id,
        enableLiveActivity: true,
        reminderTimeInSeconds: 3600,
        enableBreaks: true,
        domains: ["example.com", "social.example"]
      ),
      displayTime: 900,
      isPauseActive: true,
      onStartTapped: {}
    )
  }
  .argonAmbience()
  .environmentObject(ThemeManager.shared)
}
