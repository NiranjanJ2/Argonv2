import XCTest

@testable import foqos

final class ArgonLockDecisionTests: XCTestCase {
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
