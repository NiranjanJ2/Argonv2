import SwiftUI

/// Things that stop blocking from working. Red as the sheet allows it: soft
/// red words on a faint red wash inside a red construction line, one row per
/// alert. They were 148pt solid red tiles in a sideways scroll.
struct HomeAlertsView: View {
  let alerts: [HomeAlert]
  let onAlertTapped: (HomeAlert) -> Void

  var body: some View {
    if !alerts.isEmpty {
      VStack(alignment: .leading, spacing: 10) {
        SectionTitle("Alerts")
          .padding(.horizontal, Argon.overshoot)

        VStack(spacing: Argon.overshoot * 2) {
          ForEach(alerts) { alert in
            Button {
              onAlertTapped(alert)
            } label: {
              HomeAlertCard(alert: alert)
            }
            .buttonStyle(.plain)
            .padding(Argon.overshoot)
          }
        }
      }
    }
  }
}

private struct HomeAlertCard: View {
  let alert: HomeAlert

  var body: some View {
    HStack(spacing: 12) {
      Image(systemName: alert.iconName)
        .font(.body.weight(.semibold))
        .frame(width: 22)

      VStack(alignment: .leading, spacing: 2) {
        Text(alert.title)
          .font(Argon.body.weight(.semibold))
          .lineLimit(2)
        Text(alert.message)
          .font(Argon.label)
          .foregroundStyle(Argon.overdue.opacity(0.8))
          .lineLimit(2)
          .fixedSize(horizontal: false, vertical: true)
      }
      .frame(maxWidth: .infinity, alignment: .leading)

      Image(systemName: "chevron.right")
        .font(.caption.weight(.semibold))
    }
    .padding(.horizontal, 14)
    .padding(.vertical, 12)
    .frame(maxWidth: .infinity, alignment: .leading)
    .draftAlarm()
    .contentShape(Rectangle())
    .accessibilityElement(children: .combine)
    .accessibilityLabel(alert.title)
    .accessibilityHint(alert.message)
  }
}

struct HomeAlertDetailView: View {
  @Environment(\.dismiss) private var dismiss
  @EnvironmentObject var themeManager: ThemeManager

  let alert: HomeAlert
  let disabledReason: String?
  let onPrimaryAction: () -> Void

  private var canRunPrimaryAction: Bool { disabledReason == nil }

  var body: some View {
    NavigationStack {
      VStack(spacing: 22) {
        Spacer(minLength: 10)

        Image(systemName: alert.iconName)
          .font(.system(size: 34, weight: .medium))
          .foregroundStyle(Argon.overdue)
          .frame(width: 80, height: 80)
          .draftAlarm()
          .padding(Argon.overshoot)

        VStack(spacing: 10) {
          Text(alert.title)
            .font(.title2.weight(.semibold).width(.expanded))
            .foregroundStyle(Argon.Tone.primary)
            .multilineTextAlignment(.center)
            // Expanded width runs long; wrap rather than truncate.
            .fixedSize(horizontal: false, vertical: true)

          Text(alert.detailMessage)
            .font(Argon.body)
            .foregroundStyle(Argon.Tone.secondary)
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 28)

        if let disabledReason {
          Text(disabledReason)
            .font(Argon.label)
            .foregroundStyle(Argon.Tone.secondary)
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .draftBox()
            .padding(.horizontal, Argon.overshoot)
        }

        Spacer(minLength: 12)

        ActionButton(
          title: alert.primaryActionTitle,
          backgroundColor: themeManager.themeColor,
          isDisabled: !canRunPrimaryAction,
          action: runPrimaryAction
        )
      }
      .padding()
      .argonAmbience()
      .navigationTitle("Details")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .topBarLeading) {
          Button {
            dismiss()
          } label: {
            Image(systemName: "xmark")
          }
          .accessibilityLabel("Cancel")
        }
      }
    }
  }

  private func runPrimaryAction() {
    onPrimaryAction()
    dismiss()
  }
}

#Preview {
  HomeAlertsView(
    alerts: [
      HomeAlert(
        type: .screenTimeAccess,
        title: "Screen Time access needed",
        message: "Blocking is paused until access is restored.",
        detailMessage:
          "Foqos needs Screen Time access to block apps and websites. Grant access again to restore blocking.",
        primaryActionTitle: "Allow Screen Time Access",
        iconName: "exclamationmark.shield.fill"
      )
    ],
    onAlertTapped: { _ in }
  )
  .padding()
  .environmentObject(ThemeManager.shared)
}
