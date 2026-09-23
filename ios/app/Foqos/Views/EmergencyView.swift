import SwiftUI

struct EmergencyView: View {
  @Environment(\.modelContext) private var context
  @Environment(\.dismiss) private var dismiss

  @EnvironmentObject var strategyManager: StrategyManager

  private var emergencyUnblocksRemaining: Int { strategyManager.getRemainingEmergencyUnblocks() }
  private var hasRemaining: Bool { strategyManager.getRemainingEmergencyUnblocks() > 0 }

  @State private var isPerformingEmergencyUnblock: Bool = false

  var body: some View {
    ScrollView {
      VStack(spacing: 20) {
        header

        statusCard
      }
      .padding(.horizontal, Argon.margin)
      .padding(.vertical)
    }
    .argonAmbience()
    .onAppear {
      strategyManager.checkAndResetEmergencyUnblocks()
    }
  }

  private var header: some View {
    VStack(alignment: .leading, spacing: 12) {
      HStack {
        Text("Emergency access")
          .font(.title2.weight(.semibold).width(.expanded))
          .foregroundStyle(Argon.Tone.primary)

        Spacer()

        HStack(spacing: 8) {
          HStack(spacing: 6) {
            Image(systemName: "clock.arrow.circlepath")
              .font(.caption)
              .foregroundStyle(Argon.Tone.faint)

            Group {
              if let nextResetDate = strategyManager.getNextResetDate() {
                let timeUntilReset = nextResetDate.timeIntervalSinceNow
                if timeUntilReset <= 24 * 60 * 60 {  // Less than 24 hours
                  let hoursRemaining = max(1, Int(ceil(timeUntilReset / 3600)))
                  Text("Resets in \(hoursRemaining)h")
                    .font(.caption)
                } else {
                  Text("Resets \(nextResetDate, format: .dateTime.month().day())")
                    .font(.caption)
                }
              }
            }
          }
          .padding(.vertical, 6)

          Menu {
            let currentPeriod = strategyManager.getResetPeriodInWeeks()

            Button {
              strategyManager.setResetPeriodInWeeks(2)
            } label: {
              if currentPeriod == 2 {
                Label("2 weeks", systemImage: "checkmark")
              } else {
                Text("2 weeks")
              }
            }

            Button {
              strategyManager.setResetPeriodInWeeks(4)
            } label: {
              if currentPeriod == 4 {
                Label("4 weeks", systemImage: "checkmark")
              } else {
                Text("4 weeks")
              }
            }

            Button {
              strategyManager.setResetPeriodInWeeks(6)
            } label: {
              if currentPeriod == 6 {
                Label("6 weeks", systemImage: "checkmark")
              } else {
                Text("6 weeks")
              }
            }

            Button {
              strategyManager.setResetPeriodInWeeks(8)
            } label: {
              if currentPeriod == 8 {
                Label("8 weeks", systemImage: "checkmark")
              } else {
                Text("8 weeks")
              }
            }
          } label: {
            Image(systemName: "gearshape")
              .font(.footnote)
              .foregroundStyle(Argon.accent)
              .frame(width: 32, height: 32)
              .draftBox(overshoot: 4)
          }
        }
      }

      Text(
        "Tap the glass to reveal the emergency unblock button. Use only when absolutely necessary."
      )
      .font(Argon.detail)
      .foregroundStyle(Argon.Tone.secondary)
    }
    .padding(.top, 16)
    .frame(maxWidth: .infinity, alignment: .leading)
  }

  private var statusCard: some View {
    VStack(alignment: .leading, spacing: 12) {
      HStack(spacing: 12) {
        Image(systemName: hasRemaining ? "shield.lefthalf.filled" : "shield.slash")
          .font(.title3)
          .foregroundStyle(hasRemaining ? Argon.accent : Argon.overdue)
        VStack(alignment: .leading, spacing: 4) {
          Text("Unblocks remaining")
            .font(Argon.caption)
            .foregroundStyle(Argon.Tone.secondary)
          Text("\(emergencyUnblocksRemaining)")
            .font(.title2.weight(.semibold).monospacedDigit())
            .foregroundStyle(hasRemaining ? Argon.Tone.primary : Argon.overdue)
        }
        Spacer()
      }

      Text("You have a limited number of emergency unblocks.")
        .font(Argon.label)
        .foregroundStyle(Argon.Tone.secondary)

      BreakGlassButton(tapsToShatter: 3) {
        ActionButton(
          title: "Emergency Unblock",
          backgroundColor: Argon.overdue,
          iconName: "exclamationmark.triangle",
          isLoading: isPerformingEmergencyUnblock,
          isDisabled: !hasRemaining
        ) {
          performEmergencyUnblock()
        }
      }
      .frame(height: 62)

      if !hasRemaining {
        Text("No emergency unblocks remaining. You're out of luck.")
          .font(Argon.label)
          .foregroundStyle(Argon.overdue)
          .padding(.horizontal, 8).padding(.vertical, 6)
          .background(Argon.overdue.opacity(0.10))
      } else {
        Text("This will reduce your remaining count by 1.")
          .font(Argon.label)
          .foregroundStyle(Argon.Tone.faint)
      }
    }
    .padding(16)
    .draftBox()
  }

  private func performEmergencyUnblock() {
    isPerformingEmergencyUnblock = true

    DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
      strategyManager.emergencyUnblock(context: context)
      isPerformingEmergencyUnblock = false
      dismiss()
    }
  }
}

struct EmergencyPreviewSheetHost: View {
  @State private var show: Bool = true

  var body: some View {
    Color.clear
      .sheet(isPresented: $show) {
        NavigationView { EmergencyView() }
          .presentationDetents([.medium, .large])
          .presentationDragIndicator(.visible)
      }
  }
}

#Preview {
  EmergencyPreviewSheetHost()
    .environmentObject(StrategyManager())
    .defaultAppStorage(UserDefaults(suiteName: "preview")!)
}
