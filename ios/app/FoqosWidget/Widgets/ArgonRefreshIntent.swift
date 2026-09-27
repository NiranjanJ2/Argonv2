import AppIntents
import WidgetKit

/// The widget's refresh button: pull the board from the server now.
///
/// Lives in the widget target only, so it always runs in the widget's own
/// process — whether or not the app is running — and does its own fetch.
struct ArgonRefreshIntent: AppIntent {
  static var title: LocalizedStringResource = "Refresh Argon"
  static var description = IntentDescription("Fetch the latest board from Argon.")

  func perform() async throws -> some IntentResult {
    await ArgonSnapshot.refresh()
    return .result()
  }
}
