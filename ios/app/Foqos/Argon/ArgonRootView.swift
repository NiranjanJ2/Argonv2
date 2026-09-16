import SwiftUI

/// The Argon half of the app. Foqos keeps its own screens; this is added
/// alongside, so removing Argon is deleting these files and one line.
struct ArgonRootView: View {
  let store: ArgonStore
  @Environment(\.scenePhase) private var phase
  @State private var tab = Tab.today

  enum Tab: Hashable { case today, chat, settings }

  var body: some View {
    TabView(selection: $tab) {
      ArgonTodayView(store: store)
        .tabItem { Label("Today", systemImage: "list.bullet") }
        .tag(Tab.today)

      ArgonChatView(store: store)
        .tabItem { Label("Argon", systemImage: "bubble.left.and.bubble.right") }
        .badge(store.state.unread)
        .tag(Tab.chat)

      ArgonSettingsView(store: store)
        .tabItem { Label("Settings", systemImage: "gearshape") }
        .tag(Tab.settings)
    }
    .tint(Argon.accent)
    // The tab bar sits on glass too, so the ambient field runs behind it
    // rather than stopping at a grey strip.
    .toolbarBackground(.ultraThinMaterial, for: .tabBar)
    .toolbarColorScheme(.dark, for: .tabBar)
    .preferredColorScheme(.dark)
    .task { await store.refresh() }
    // Refresh when he brings the app forward. Not a timer — iOS suspends those
    // in the background, so a timer refresh only fires while he is already
    // looking at the screen, which is the one moment it isn't needed.
    .onChange(of: phase) { _, new in
      if new == .active { Task { await store.refresh() } }
    }
    // Ask for notifications when he actually opens the conversation, not at
    // launch. A prompt over an empty screen, before the app has shown him
    // anything, is the reliable way to be told no — and this is the one
    // permission Argon needs, because the brief arrives by push.
    //
    // On tab *selection* rather than in the chat view's .task: SwiftUI runs
    // .task for tabs that are not on screen, so that fired at launch anyway.
    .onChange(of: tab) { _, new in
      guard new == .chat else { return }
      Task { await ArgonAppDelegate.shared.requestPushIfNeeded() }
    }
  }
}
