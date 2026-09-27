import SwiftUI
import WidgetKit

/// Today's board on the home screen. Reads the snapshot the app writes; it
/// never talks to the server itself.
struct ArgonTodayWidget: Widget {
  var body: some WidgetConfiguration {
    StaticConfiguration(kind: "ArgonToday", provider: ArgonTodayProvider()) { entry in
      ArgonTodayWidgetView(snapshot: entry.snapshot)
        // The sheet's own ground, flat. This was a system material under a
        // blue gradient in the glass theme; the drawing sheet has no
        // gradients, and a material shifts with the wallpaper so the
        // linework would read differently on every home screen. In tinted
        // or clear home screen modes the system drops this background anyway.
        .containerBackground(Argon.Ink.base, for: .widget)
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

  private var small: Bool { family == .systemSmall }
  /// The lock screen draws in one vibrant colour; lines and washes vanish.
  private var accessory: Bool { family == .accessoryRectangular }

  var body: some View {
    VStack(alignment: .leading, spacing: 5) {
      HStack(spacing: 6) {
        ArgonPulse(colour: snapshot.watching ? Argon.running : Argon.Tone.faint)
        Text(snapshot.watching ? "watching" : "off duty")
          .font(.caption2.weight(.medium).width(.expanded))
          .foregroundStyle(Argon.Tone.secondary)
        Spacer(minLength: 4)
        if snapshot.overdue > 0 {
          // Red only as a tint over red.
          ArgonPill(text: "\(snapshot.overdue) late", colour: Argon.overdue,
                    tinted: !accessory)
        }
        if !accessory {
          // Refetches from the server — which re-reads Classroom first — then
          // redraws. Lock-screen widgets are not interactive, so it stays off
          // there.
          Button(intent: ArgonRefreshIntent()) {
            Image(systemName: "arrow.clockwise")
              .font(.caption2.weight(.semibold))
              .foregroundStyle(Argon.accent)
              .frame(width: 22, height: 22)
              .contentShape(Rectangle())
          }
          .buttonStyle(.plain)
          .accessibilityLabel("Refresh")
        }
      }

      if !accessory {
        // A construction line under the status, running past the content
        // into the widget's margin like every box edge in the app.
        Rectangle().fill(Argon.line).frame(height: 1)
          .padding(.horizontal, -Argon.overshoot)
          .padding(.top, 2)
      }

      Spacer(minLength: 2)

      Text(snapshot.headline)
        .font(small ? .subheadline.weight(.semibold) : .headline)
        .foregroundStyle(Argon.Tone.primary)
        .lineLimit(small ? 3 : 2)

      if !snapshot.detail.isEmpty {
        if snapshot.detail == "overdue" {
          ArgonPill(text: snapshot.detail, colour: Argon.overdue, tinted: !accessory)
        } else {
          Text(snapshot.detail)
            .font(.caption)
            .foregroundStyle(Argon.Tone.secondary)
            .lineLimit(1)
        }
      }

      // When the board was last fetched, so a tap on refresh visibly lands.
      HStack(spacing: 4) {
        if snapshot.open > 1 { Text("\(snapshot.open) open,") }
        if snapshot.updated > .distantPast {
          Text("updated \(snapshot.updated.formatted(date: .omitted, time: .shortened))")
        }
      }
      .font(.caption2.monospacedDigit())
      .foregroundStyle(Argon.Tone.faint)
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
  }
}
