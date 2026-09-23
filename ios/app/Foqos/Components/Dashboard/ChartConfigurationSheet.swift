import SwiftUI

struct ChartConfigurationSheet: View {
  @EnvironmentObject var themeManager: ThemeManager
  @Binding var showHabitTracker: Bool
  @Binding var chartType: HabitChartType
  let onDismiss: () -> Void

  private func label(_ title: String) -> some View {
    Text(title).font(Argon.caption).foregroundStyle(Argon.Tone.secondary)
      .draftLabelRow()
  }

  var body: some View {
    NavigationStack {
      List {
        label("Visibility")
        Toggle("Show chart", isOn: $showHabitTracker)
          .font(Argon.body)
          .foregroundStyle(Argon.Tone.primary)
          .padding(.vertical, 12)
          .draftRow(.single)

        label("Chart type")
        let types = HabitChartType.allCases
        ForEach(Array(types.enumerated()), id: \.element) { i, type in
          Button {
            chartType = type
          } label: {
            HStack(alignment: .top, spacing: 12) {
              DraftCheck(on: chartType == type)
                .padding(.top, 2)

              VStack(alignment: .leading, spacing: 4) {
                Text(type.rawValue)
                  .font(Argon.body.weight(.medium))
                  .foregroundStyle(Argon.Tone.primary)
                Text(type.description)
                  .font(Argon.label)
                  .foregroundStyle(Argon.Tone.secondary)
                  .multilineTextAlignment(.leading)
                  .fixedSize(horizontal: false, vertical: true)
              }

              Spacer()
            }
            .padding(.vertical, 12)
            .contentShape(Rectangle())
          }
          .buttonStyle(.plain)
          .draftRow(.of(i, in: types.count),
                    fill: chartType == type ? Argon.accent.opacity(0.06) : .clear)
        }
      }
      .listStyle(.plain)
      .scrollContentBackground(.hidden)
      .environment(\.defaultMinListRowHeight, 0)
      .argonAmbience()
      .tint(Argon.accent)
      .navigationTitle("Manage chart")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .topBarTrailing) {
          Button {
            onDismiss()
          } label: {
            Image(systemName: "checkmark")
          }
        }
      }
    }
  }
}

#Preview {
  struct PreviewWrapper: View {
    @State private var showChart = true
    @State private var chartType: HabitChartType = .fourWeek

    var body: some View {
      ChartConfigurationSheet(
        showHabitTracker: $showChart,
        chartType: $chartType,
        onDismiss: {}
      )
      .environmentObject(ThemeManager.shared)
    }
  }

  return PreviewWrapper()
}
