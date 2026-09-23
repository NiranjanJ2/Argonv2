import SwiftUI

/// The Argon half of the app. Foqos keeps its own screens; this is added
/// alongside, so removing Argon is deleting these files and one line.
struct ArgonRootView: View {
  let store: ArgonStore
  @Environment(\.scenePhase) private var phase
  // Seeded from a default so screenshot.sh can shoot any screen. There is no
  // Simulator.app in this toolchain — only the headless runtime — so there is
  // no way to tap a tab; without this seam every screen after the first needs
  // a code edit and a rebuild to look at.
  @AppStorage("argon.setupComplete") private var setupComplete = false
  private var needsSetup: Binding<Bool> {
    Binding(get: { !setupComplete }, set: { if !$0 { setupComplete = true } })
  }

  @State private var tab = Tab(rawKey: UserDefaults.standard.string(forKey: "argon.tab"))
  /// The afternoon sheet, when there is one to show.
  @State private var plannerSheet: ArgonPlannerPayload?
  @State private var fetchingPlanner = false

  enum Tab: Hashable {
    case today, chat, focus, settings

    init(rawKey: String?) {
      switch rawKey {
      case "chat": self = .chat
      case "focus": self = .focus
      case "settings": self = .settings
      default: self = .today
      }
    }
  }

  var body: some View {
    TabView(selection: $tab) {
      ArgonTodayView(store: store)
        .tabItem { Label("Today", systemImage: "list.bullet") }
        .tag(Tab.today)

      ArgonChatView(store: store)
        .tabItem { Label("Argon", systemImage: "bubble.left.and.bubble.right") }
        .badge(store.state.unread)
        .tag(Tab.chat)

      // Foqos's own screen. Without this tab the entire blocking product —
      // profiles, sessions, NFC and QR, schedules — has no entry point:
      // `HomeView` appears nowhere else in the target except a #Preview, and
      // `ArgonStatusCard` is dead UI. Blocking is what lets Argon enforce
      // rather than ask, so losing the route to it loses the product.
      HomeView()
        .tabItem { Label("Focus", systemImage: "shield.lefthalf.filled") }
        .tag(Tab.focus)

      ArgonSettingsView(store: store)
        .tabItem { Label("Settings", systemImage: "gearshape") }
        .tag(Tab.settings)
    }
    .tint(Argon.accent)
    // First run asks which apps Argon may block, instead of sending him to
    // Foqos to hand-create a profile whose name had to match a string this
    // screen never showed him.
    .sheet(isPresented: needsSetup) {
      ArgonSetupView().interactiveDismissDisabled()
    }
    // The afternoon sheet opens itself: first time the app is up after 15:36
    // on a day not yet planned, when there is something to decide. Hung off
    // fresh state rather than launch so a push or a pull after 15:36 offers it
    // too. Swiping it away is allowed and it comes back next time — v1's
    // behaviour; only submitting closes it for the day.
    .sheet(item: $plannerSheet) { payload in
      ArgonPlannerView(store: store, payload: payload)
    }
    .onReceive(NotificationCenter.default.publisher(for: .argonStateApplied)) { _ in
      Task { await offerPlannerIfDue() }
    }
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

  /// Only in the foreground, after setup, and one fetch at a time: a silent
  /// push landing while the app is backgrounded must not queue a sheet, and
  /// two refreshes landing together must not read Classroom twice.
  private func offerPlannerIfDue() async {
    guard setupComplete, phase == .active, plannerSheet == nil, !fetchingPlanner
    else { return }
    fetchingPlanner = true
    defer { fetchingPlanner = false }
    if let payload = await store.plannerIfDue() {
      plannerSheet = payload
    }
  }
}
