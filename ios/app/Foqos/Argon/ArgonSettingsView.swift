import SwiftUI

/// Where the server lives, and whether anything is stuck.
///
/// v1 had no way to see that a write had failed or that the token was wrong;
/// the app just quietly did nothing. Everything that can go wrong between the
/// phone and the server is visible here.
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
            .font(Argon.body)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .keyboardType(.URL)
          SecureField("API token", text: $token)
            .font(Argon.body)
          Button {
            Task { await check() }
          } label: {
            HStack {
              Text("Test connection").font(Argon.body)
              if checking { Spacer(); ProgressView().controlSize(.small) }
            }
          }
          if let result {
            Text(result)
              .font(Argon.detail)
              .foregroundStyle(result.hasPrefix("OK") ? Argon.running : Argon.overdue)
          }
        }

        Section("Status") {
          LabeledContent("Connection") {
            switch store.connection {
            case .never:
              Text("never reached").foregroundStyle(Argon.Tone.faint)
            case .live(let at):
              Text("live, \(at.argonAgo)").foregroundStyle(Argon.running)
            case .stale(let at, let why):
              VStack(alignment: .trailing, spacing: 2) {
                Text("cached \(at.argonAgo)").foregroundStyle(Argon.overdue)
                Text(why).font(Argon.label).foregroundStyle(Argon.Tone.faint)
              }
            }
          }
          .font(Argon.body)

          LabeledContent("Queued writes", value: "\(store.pendingCount)")
            .font(Argon.body)

          if let budget = store.state.budget, budget.cap > 0 {
            LabeledContent("Spend this month",
                           value: String(format: "$%.2f / $%.0f", budget.spent, budget.cap))
              .font(Argon.body)
              .foregroundStyle(budget.isNearCap ? Argon.overdue : Argon.Tone.primary)
            LabeledContent("Prompt cache",
                           value: String(format: "%.0f%%", budget.cachedFraction * 100))
              .font(Argon.body)
          }
        }

        Section {
          Button("Retry queued writes") { Task { await store.flush() } }
            .font(Argon.body)
            .disabled(store.pendingCount == 0)
          Button("Refresh now") { Task { await store.refresh() } }
            .font(Argon.body)
        } footer: {
          Text("Writes are kept on the phone until the server accepts them, "
               + "so a tap is never lost to a dropped connection.")
            .font(Argon.label)
        }
      }
      .scrollContentBackground(.hidden)
      .navigationTitle("Settings")
      .navigationBarTitleDisplayMode(.large)
      .toolbarBackground(.hidden, for: .navigationBar)
    }
    .argonAmbience(ticking: store.state.ticking)
    .tint(Argon.accent)
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
