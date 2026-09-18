import SwiftUI
import WidgetKit

/// Today's board on the home screen. Reads the snapshot the app writes; it
/// never talks to the server itself.
struct ArgonTodayWidget: Widget {
  var body: some WidgetConfiguration {
    StaticConfiguration(kind: "ArgonToday", provider: ArgonTodayProvider()) { entry in
      ArgonTodayWidgetView(snapshot: entry.snapshot)
        // Glass rather than a painted panel. `.fill.tertiary` is the system's
        // adaptive widget material: it picks up the wallpaper behind it and
        // follows the home screen's own tinting, which a solid colour fights.
        // The gradient over it is what keeps it Argon's blue instead of a
        // generic grey pane — low enough opacity that the wallpaper still
        // reads through.
        .containerBackground(for: .widget) {
          Rectangle()
            .fill(.fill.tertiary)
            .overlay {
              LinearGradient(
                colors: [ArgonWidgetPalette.accent.opacity(0.28),
                         ArgonWidgetPalette.accent.opacity(0.06)],
                startPoint: .topLeading, endPoint: .bottomTrailing)
            }
        }
    }
    .configurationDisplayName("Argon")
    .description("What's due, and whether Argon is watching.")
    .supportedFamilies([.systemSmall, .systemMedium, .accessoryRectangular])
  }
}

struct ArgonTodayEntry: TimelineEntry {
  let date: Date
  let snapshot: ArgonSnapshot
}

struct ArgonTodayProvider: TimelineProvider {
  func placeholder(in context: Context) -> ArgonTodayEntry {
    ArgonTodayEntry(date: Date(), snapshot: .empty)
  }

  func getSnapshot(in context: Context, completion: @escaping (ArgonTodayEntry) -> Void) {
    completion(ArgonTodayEntry(date: Date(), snapshot: ArgonSnapshot.load()))
  }

  /// One entry, refreshed in fifteen minutes. The app also reloads timelines
  /// the moment it writes a new snapshot, so this is only the floor.
  func getTimeline(in context: Context, completion: @escaping (Timeline<ArgonTodayEntry>) -> Void) {
    let entry = ArgonTodayEntry(date: Date(), snapshot: ArgonSnapshot.load())
    completion(Timeline(entries: [entry],
                        policy: .after(Date().addingTimeInterval(15 * 60))))
  }
}

struct ArgonTodayWidgetView: View {
  let snapshot: ArgonSnapshot
  @Environment(\.widgetFamily) private var family

  var body: some View {
    VStack(alignment: .leading, spacing: 5) {
      HStack(spacing: 6) {
        Circle()
          .fill(snapshot.watching ? ArgonWidgetPalette.running
                                  : ArgonWidgetPalette.mutedInk)
          .frame(width: 6, height: 6)
        Text(snapshot.watching ? "watching" : "off duty")
          .font(.system(size: 10, weight: .medium))
          .foregroundStyle(ArgonWidgetPalette.mutedInk)
        Spacer()
        if snapshot.overdue > 0 {
          Text("\(snapshot.overdue)")
            .font(.system(size: 10, weight: .semibold))
            .foregroundStyle(ArgonWidgetPalette.danger)
        }
      }

      Spacer(minLength: 2)

      Text(snapshot.headline)
        .font(.system(size: family == .systemSmall ? 14 : 16, weight: .semibold))
        .foregroundStyle(ArgonWidgetPalette.ink)
        .lineLimit(family == .systemSmall ? 3 : 2)

      if !snapshot.detail.isEmpty {
        Text(snapshot.detail)
          .font(.system(size: 11))
          .foregroundStyle(snapshot.detail == "overdue" ? ArgonWidgetPalette.danger
                                                        : ArgonWidgetPalette.mutedInk)
          .lineLimit(1)
      }

      if snapshot.open > 1 {
        Text("\(snapshot.open) open")
          .font(.system(size: 10))
          .foregroundStyle(ArgonWidgetPalette.mutedInk)
      }
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
  }
}
