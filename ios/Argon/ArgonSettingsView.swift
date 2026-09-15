import SwiftUI

/// Where the server lives, and whether anything is stuck.
///
/// v1 had no way to see that a write had failed or that the token was wrong;
/// the app just quietly did nothing. Everything that can go wrong between the
/// phone and the server is visible on this screen.
struct ArgonSettingsView: View {
  let store: ArgonStore
  @AppStorage("argon.base") private var base = "http://192.168.68.72:3997"
  @AppStorage("argon.token") private var token = ""
  @State private var checking = false
  @State private var result: String?

  var body: some View {
    NavigationStack {
      Form {
        Section("Server") {
          TextField("http://host:port", text: $base)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .keyboardType(.URL)
          SecureField("API token", text: $token)
          Button {
            Task { await check() }
          } label: {
            HStack {
              Text("Test connection")
              if checking { Spacer(); ProgressView().controlSize(.mini) }
            }
          }
          if let result {
            Text(result).font(.footnote)
              .foregroundStyle(result.hasPrefix("OK") ? .green : .orange)
          }
        }

        Section("Status") {
          LabeledContent("Connection") {
            switch store.connection {
            case .never: Text("never reached").foregroundStyle(.secondary)
            case .live(let at): Text("live, \(at.argonAgo)").foregroundStyle(.green)
            case .stale(let at, let why):
              VStack(alignment: .trailing) {
                Text("cached \(at.argonAgo)").foregroundStyle(.orange)
                Text(why).font(.caption2).foregroundStyle(.secondary)
              }
            }
          }
          LabeledContent("Queued writes", value: "\(store.pendingCount)")
          if let budget = store.state.budget, budget.cap > 0 {
            LabeledContent("Spend this month",
                           value: String(format: "$%.2f / $%.0f", budget.spent, budget.cap))
              .foregroundStyle(budget.isNearCap ? .orange : .primary)
            LabeledContent("Prompt cache",
                           value: String(format: "%.0f%%", budget.cachedFraction * 100))
          }
        }

        Section {
          Button("Retry queued writes") { Task { await store.flush() } }
            .disabled(store.pendingCount == 0)
          Button("Refresh now") { Task { await store.refresh() } }
        } footer: {
          Text("Writes are kept on the phone until the server accepts them, "
               + "so a tap is never lost to a dropped connection.")
        }
      }
      .navigationTitle("Settings")
    }
  }

  private func check() async {
    checking = true
    defer { checking = false }
    guard let url = URL(string: base) else { result = "That isn't a URL."; return }
    await ArgonAppDelegate.shared.reconfigure()
    await store.refresh()
    result = store.connection.isLive
      ? "OK — reached \(url.host() ?? base)"
      : (store.failure ?? "Couldn't reach it.")
  }
}
