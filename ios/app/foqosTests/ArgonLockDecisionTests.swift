import XCTest

@testable import foqos

final class ArgonLockDecisionTests: XCTestCase {
  func testHoldingSessionIdentityProtectsOtherSessions() {
    XCTAssertTrue(ArgonRoutineSettings.isHoldingSession(recorded: "holding", active: "holding"))
    XCTAssertFalse(ArgonRoutineSettings.isHoldingSession(recorded: "holding", active: "manual"))
    XCTAssertFalse(ArgonRoutineSettings.isHoldingSession(recorded: nil, active: "manual"))
    XCTAssertFalse(ArgonRoutineSettings.mayEndHoldingSession(recorded: "holding", active: "task"))
    XCTAssertFalse(ArgonRoutineSettings.mayEndHoldingSession(recorded: "", active: "manual"))
    XCTAssertTrue(ArgonRoutineSettings.mayEndHoldingSession(recorded: nil, active: "legacy"))
  }

  func testShortRemainingLockDoesNotCreateAnOverlongSession() {
    XCTAssertFalse(ArgonLockReconciler.canStartTimedSession(secondsLeft: 14 * 60))
    XCTAssertTrue(ArgonLockReconciler.canStartTimedSession(secondsLeft: 15 * 60))
  }
  func testEmptySelectionIsAReportedFailure() {
    XCTAssertEqual(
      ArgonLockWindow.armResult(hasSelection: false, schedulingError: nil),
      .failed("the blocking profile has no apps, categories, or websites")
    )
  }

  func testSchedulingFailureIsAReportedFailure() {
    XCTAssertEqual(
      ArgonLockWindow.armResult(hasSelection: true, schedulingError: "interval too short"),
      .failed("interval too short")
    )
  }

  func testAcceptedScheduleIsAcknowledged() {
    XCTAssertEqual(
      ArgonLockWindow.armResult(hasSelection: true, schedulingError: nil),
      .armed
    )
  }

  func testLiveLockStillAppliesWhenOnlySchedulingFails() {
    XCTAssertTrue(ArgonLockWindow.canApplyImmediately(
      after: .failed("interval too short")))
    XCTAssertFalse(ArgonLockWindow.canApplyImmediately(
      after: .failed("the blocking profile has no apps, categories, or websites")))
  }
}
