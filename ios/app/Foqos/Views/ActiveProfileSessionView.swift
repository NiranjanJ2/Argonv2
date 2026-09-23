import FamilyControls
import SwiftUI
import UIKit

struct ActiveProfileSessionView: View {
  @Environment(\.dismiss) private var dismiss
  @EnvironmentObject private var strategyManager: StrategyManager
  @EnvironmentObject private var themeManager: ThemeManager

  let profile: BlockedProfiles
  let elapsedTime: TimeInterval
  let displayTime: TimeInterval
  let isBreakAvailable: Bool
  let isBreakActive: Bool
  let isPauseActive: Bool
  let onBreakTapped: () -> Void
  let onStopTapped: () -> Void

  @State private var showEmergencyView = false
  @State private var showProfileInsights = false
  @State private var showingAlert = false
  @State private var alertMessage = ""
  @State private var focusMessageIndex = Self.initialFocusMessageIndex()

  private let focusMessageTimer = Timer.publish(every: 10, on: .main, in: .common).autoconnect()

  private var showStopButton: Bool {
    profile.showStopButton(elapsedTime: elapsedTime)
  }

  private var stopButtonAction: BlockingStrategySessionAction {
    blockingStrategy?.activeSessionAction(isPauseActive: isPauseActive) ?? .stop()
  }

  private var breakButtonTitle: String {
    "Hold to " + (isBreakActive ? "Stop Break" : "Start Break")
  }

  private var focusMessage: String {
    guard FocusMessages.messages.indices.contains(focusMessageIndex) else {
      return FocusMessages.getRandomMessage()
    }
    return FocusMessages.messages[focusMessageIndex]
  }

  private var strategyName: String {
    guard let strategyId = profile.blockingStrategyId else {
      return "No Strategy"
    }
    return StrategyManager.getStrategyFromId(id: strategyId).name
  }

  private var blockingStrategy: BlockingStrategy? {
    guard let strategyId = profile.blockingStrategyId else {
      return nil
    }
    return StrategyManager.getStrategyFromId(id: strategyId)
  }

  private var isSoftUnblockStrategy: Bool {
    guard let strategyId = profile.blockingStrategyId else { return false }
    return [
      NFCSoftUnblockBlockingStrategy.id,
      QRSoftUnblockBlockingStrategy.id,
    ].contains(strategyId)
  }

  private var supportingTextColor: Color {
    ArgonPalette.mutedInk
  }

  var body: some View {
    ZStack {
      background

      VStack(alignment: .leading, spacing: 0) {
        header
          .padding(.horizontal, Argon.overshoot)

        ScrollView {
          timerSection
            .padding(.top, 36)
            .padding(.bottom, 36)
            .padding(.horizontal, Argon.overshoot)
            .frame(maxWidth: .infinity)
        }
        .scrollIndicators(.hidden)
        .scrollBounceBehavior(.basedOnSize)

        actionSection
          // Buttons pad 6 for their own overshoot; this lands their lines on
          // the margin with everything else.
          .padding(.horizontal, Argon.overshoot - 6)
      }
      .padding(.horizontal, Argon.margin - Argon.overshoot)
      .padding(.top, 18)
      .padding(.bottom, 20)
    }
    .sheet(isPresented: $showEmergencyView) {
      EmergencyView()
        .presentationDetents([.height(350), .large])
    }
    .sheet(isPresented: $showProfileInsights) {
      ProfileInsightsView(profile: profile)
    }
    .sheet(isPresented: $strategyManager.showCustomStrategyView) {
      BlockingStrategyActionView(
        customView: strategyManager.customStrategyView,
        presentationDetents: strategyManager.customStrategyViewPresentationDetents
      )
    }
    .onReceive(focusMessageTimer) { _ in
      rotateFocusMessage()
    }
    .onReceive(strategyManager.$errorMessage) { errorMessage in
      guard let message = errorMessage else { return }
      alertMessage = message
      showingAlert = true
    }
    .alert("Whoops", isPresented: $showingAlert) {
      Button("OK", role: .cancel) {
        dismissAlert()
      }
    } message: {
      Text(alertMessage)
    }
  }

  /// The sheet's flat ground. There was a blue radial bloom rising from the
  /// bottom edge; the running task's box carries the blue now.
  private var background: some View {
    ArgonBackdrop()
  }

  private var header: some View {
    HStack(alignment: .top, spacing: 16) {
      VStack(alignment: .leading, spacing: 8) {
        Text(profile.name)
          .font(Argon.screenTitle)
          .foregroundStyle(Argon.Tone.primary)
          .lineLimit(2)
          .minimumScaleFactor(0.72)

        if let statusMessage, let statusIconName {
          HStack(spacing: 6) {
            Image(systemName: statusIconName)
              .font(.subheadline.weight(.semibold))
              .foregroundStyle(Argon.accent)

            Text(statusMessage)
              .font(Argon.detail.weight(.medium))
              .foregroundStyle(Argon.Tone.secondary)
          }
        }
      }

      Spacer(minLength: 12)

      HStack(spacing: 14) {
        DraftIconButton(systemName: "chart.line.uptrend.xyaxis",
                        accessibilityLabel: "Insights") {
          showProfileInsights = true
        }
        DraftIconButton(systemName: "xmark", colour: Argon.Tone.secondary,
                        accessibilityLabel: "Close") {
          dismiss()
        }
      }
      .padding(.top, 6)
    }
  }

  private var statusMessage: String? {
    if isPauseActive {
      return "Paused"
    }
    if isBreakActive {
      return "On a Break"
    }
    return nil
  }

  private var statusIconName: String? {
    if isPauseActive {
      return "pause"
    }
    if isBreakActive {
      return "cup.and.saucer"
    }
    return nil
  }

  /// The running session as Today draws a running task: one box, blue line,
  /// faint blue wash. It was an orb with a clock floating over it, the clock
  /// under a heavy drop shadow.
  private var timerSection: some View {
    VStack(alignment: .leading, spacing: 18) {
      HStack(spacing: 8) {
        ArgonPulse()
        BlockingStrategySymbol(strategy: blockingStrategy)
          .font(.footnote.weight(.semibold))
          .foregroundStyle(Argon.accent)
          .accessibilityHidden(true)
        Text(strategyName)
          .font(Argon.caption)
          .foregroundStyle(Argon.Tone.secondary)
      }

      Text(DateFormatters.formatDurationClock(displayTime))
        .font(.argonDisplay(60, weight: .semibold))
        .monospacedDigit()
        .foregroundStyle(Argon.Tone.primary)
        .lineLimit(1)
        .minimumScaleFactor(0.55)
        .contentTransition(.numericText())
        .animation(.default, value: displayTime)

      ArgonDivider()

      Text(focusMessage)
        .font(Argon.body)
        .foregroundStyle(Argon.Tone.secondary)
        .lineLimit(2)
        .fixedSize(horizontal: false, vertical: true)
        .contentTransition(.opacity)
        .animation(.easeInOut(duration: 0.35), value: focusMessage)

      if isSoftUnblockStrategy {
        SoftUnblockActiveGrantsCard(profileId: profile.id)
          .padding(.top, 4)
      }
    }
    .padding(20)
    .frame(maxWidth: .infinity, alignment: .leading)
    .draftBox(stroke: Argon.accent.opacity(0.55), fill: Argon.accent.opacity(0.06))
  }

  private var actionSection: some View {
    VStack(spacing: 12) {
      if !isPauseActive && isBreakAvailable {
        ActiveSessionActionButton(
          title: breakButtonTitle,
          iconName: "cup.and.heat.waves.fill",
          imageName: "CoffeeStickerIcon",
          role: isBreakActive ? .warning : .standard,
          requiresLongPress: true,
          action: onBreakTapped
        )
      }

      HStack(spacing: 12) {
        if profile.enableEmergencyUnblock {
          ActiveSessionActionButton(
            title: "Emergency",
            iconName: "exclamationmark.triangle.fill",
            role: .destructive,
            action: {
              showEmergencyView = true
            }
          )
        }

        if showStopButton {
          ActiveSessionActionButton(
            title: stopButtonAction.title,
            iconName: stopButtonAction.systemImageName,
            imageName: stopButtonAction.assetImageName,
            role: .standard,
            action: onStopTapped
          )
        }
      }
    }
  }

  private func rotateFocusMessage() {
    guard !FocusMessages.messages.isEmpty else { return }
    withAnimation(.easeInOut(duration: 0.35)) {
      focusMessageIndex = (focusMessageIndex + 1) % FocusMessages.messages.count
    }
  }

  private func dismissAlert() {
    showingAlert = false
    strategyManager.errorMessage = nil
  }

  private static func initialFocusMessageIndex() -> Int {
    guard !FocusMessages.messages.isEmpty else { return 0 }
    return Int.random(in: 0..<FocusMessages.messages.count)
  }
}

private struct SoftUnblockActiveGrantsCard: View {
  private static let visibleGrantLimit = 3

  let profileId: UUID

  var body: some View {
    TimelineView(.periodic(from: .now, by: 1)) { timeline in
      let activeGrants = SoftUnblockGrantStore.activeGrants(
        for: profileId,
        at: timeline.date
      )
      .sorted { lhs, rhs in
        if lhs.expiresAt == rhs.expiresAt {
          return lhs.createdAt < rhs.createdAt
        }
        return lhs.expiresAt < rhs.expiresAt
      }
      let visibleGrants = Array(activeGrants.prefix(Self.visibleGrantLimit))
      let overflowCount = activeGrants.count - visibleGrants.count

      if !activeGrants.isEmpty {
        VStack(spacing: 0) {
          ForEach(Array(visibleGrants.enumerated()), id: \.element.id) { index, grant in
            if index > 0 {
              ArgonDivider()
            }

            SoftUnblockActiveGrantRow(
              grant: grant,
              date: timeline.date
            )
          }

          if overflowCount > 0 {
            ArgonDivider()

            Text("+\(overflowCount) more active")
              .font(Argon.label)
              .foregroundStyle(Argon.Tone.secondary)
              .frame(maxWidth: .infinity, alignment: .leading)
              .padding(.vertical, 10)
          }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 4)
        .overlay(Rectangle().strokeBorder(Argon.line, lineWidth: 1))
        .transition(.move(edge: .bottom).combined(with: .opacity))
        .animation(.easeInOut(duration: 0.25), value: activeGrants.map(\.id))
      }
    }
  }
}

private struct SoftUnblockActiveGrantRow: View {
  let grant: SoftUnblockGrant
  let date: Date

  private var remainingSeconds: Int {
    max(Int(ceil(grant.expiresAt.timeIntervalSince(date))), 0)
  }

  private var countdownText: String {
    String(
      format: "%02d:%02d",
      remainingSeconds / 60,
      remainingSeconds % 60
    )
  }

  private var accessibilityCountdownText: String {
    let minutes = remainingSeconds / 60
    let seconds = remainingSeconds % 60
    let minuteLabel = minutes == 1 ? "minute" : "minutes"
    let secondLabel = seconds == 1 ? "second" : "seconds"
    return "\(minutes) \(minuteLabel), \(seconds) \(secondLabel) remaining"
  }

  var body: some View {
    HStack(spacing: 12) {
      resourceLabel
        .font(.subheadline)
        .fontWeight(.semibold)
        .lineLimit(1)
        .minimumScaleFactor(0.8)

      Spacer(minLength: 8)

      Text(countdownText)
        .font(Argon.detail.weight(.semibold).monospacedDigit())
        .foregroundStyle(Argon.accent)
        .contentTransition(.numericText(countsDown: true))
        .accessibilityLabel(accessibilityCountdownText)
    }
    .padding(.vertical, 10)
  }

  @ViewBuilder
  private var resourceLabel: some View {
    switch grant.resource {
    case .application(let token):
      Label(token)
    case .category(let token):
      Label(token)
    }
  }
}

private enum ActiveSessionActionRole {
  case standard
  case warning
  case destructive
}

private struct ActiveSessionActionButton: View {
  let title: String
  let iconName: String
  var imageName: String? = nil
  let role: ActiveSessionActionRole
  var requiresLongPress = false
  let action: () -> Void

  @State private var isPressed = false

  /// Blue for everything he chooses to do; red — as a tint, over a red wash
  /// — only for the emergency exit.
  private var foregroundColor: Color {
    switch role {
    case .standard, .warning:
      return Argon.accent
    case .destructive:
      return Argon.overdue
    }
  }

  private var wash: Double { role == .warning ? 0.16 : 0.08 }

  var body: some View {
    Group {
      if requiresLongPress {
        label
          .opacity(isPressed ? 0.7 : 1)
          .onLongPressGesture(
            minimumDuration: 0.8,
            pressing: { pressing in
              isPressed = pressing
            },
            perform: triggerAction
          )
      } else {
        Button(action: triggerAction) {
          label
        }
        .buttonStyle(ActiveSessionPressStyle())
      }
    }
  }

  private var label: some View {
    HStack(spacing: 8) {
      icon

      Text(title)
        .font(Argon.body.weight(.semibold))
        .lineLimit(1)
        .minimumScaleFactor(0.82)
    }
    .frame(maxWidth: .infinity)
    .frame(height: 52)
    .foregroundStyle(foregroundColor)
    .draftBox(stroke: foregroundColor.opacity(0.6), fill: foregroundColor.opacity(wash),
              overshoot: 6)
    .contentShape(Rectangle())
    .padding(6)
  }

  @ViewBuilder
  private var icon: some View {
    // Always the SF Symbol: the colour stickers (`imageName`) do not take
    // the button's tint.
    Image(systemName: iconName)
      .font(.callout.weight(.semibold))
  }

  private func triggerAction() {
    UIImpactFeedbackGenerator(style: .light).impactOccurred()
    action()
  }
}

private struct ActiveSessionPressStyle: ButtonStyle {
  func makeBody(configuration: Configuration) -> some View {
    configuration.label
      .opacity(configuration.isPressed ? 0.7 : 1)
  }
}

#Preview {
  ActiveProfileSessionView(
    profile: BlockedProfiles(name: "Work Focus"),
    elapsedTime: 3665,
    displayTime: 3665,
    isBreakAvailable: true,
    isBreakActive: false,
    isPauseActive: false,
    onBreakTapped: {},
    onStopTapped: {}
  )
  .environmentObject(StrategyManager())
  .environmentObject(ThemeManager.shared)
}
