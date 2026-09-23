import XCTest
@testable import ArgonSync

/// v1's renderer tests, ported, plus the task resolution v2 added: which
/// button can act, given what is on the board.
final class ArgonMarkdownTests: XCTestCase {
  func testAChecklistIsCheckboxesNotBullets() {
    // "- [ ] x" matches the bullet pattern too; the checkbox is the more
    // specific reading and has to win.
    let blocks = ArgonMarkdown.blocks("- [ ] HW 9\n- [x] AP Chem\n- Physics", messageID: "m")

    XCTAssertEqual(blocks, [
      .checkbox(id: "m:0", text: "HW 9", checked: false),
      .checkbox(id: "m:1", text: "AP Chem", checked: true),
      .bullet("Physics"),
    ])
  }

  func testNumberedListsKeepTheirNumbers() {
    let blocks = ArgonMarkdown.blocks("1. first\n2) second", messageID: "m")

    XCTAssertEqual(blocks, [.numbered(index: 1, text: "first"),
                            .numbered(index: 2, text: "second")])
  }

  func testHeadingsAndRulesAreStructureNotText() {
    let blocks = ArgonMarkdown.blocks("## Tonight\n---\nplain line", messageID: "m")

    XCTAssertEqual(blocks, [.heading("Tonight"), .divider, .paragraph("plain line")])
  }

  func testBlankLinesAreDroppedNotRendered() {
    XCTAssertEqual(ArgonMarkdown.blocks("one\n\n\ntwo", messageID: "m"),
                   [.paragraph("one"), .paragraph("two")])
  }

  func testAStrayAsteriskIsNotSwallowed() {
    // Falling back to the raw string matters more than styling: dropping part
    // of what Argon said is the worse failure.
    let rendered = String(ArgonMarkdown.inline("2 * 3 is 6 and **this** is bold").characters)

    XCTAssertTrue(rendered.contains("2 * 3 is 6"))
    XCTAssertTrue(rendered.contains("this"))
    XCTAssertFalse(rendered.contains("**"))
  }

  func testCheckboxIdsAreStablePerMessageLine() {
    // The tick is stored against this id. If it moved between renders, a
    // checked box would come back empty on the next poll. The line number
    // counts blank lines, so it is the line in the raw text.
    let first = ArgonMarkdown.blocks("- [ ] a\n\n- [ ] b", messageID: "seq:7")
    let again = ArgonMarkdown.blocks("- [ ] a\n\n- [ ] b", messageID: "seq:7")

    XCTAssertEqual(first, again)
    XCTAssertEqual(first[1], .checkbox(id: "seq:7:2", text: "b", checked: false))
  }
}

final class ArgonActionTests: XCTestCase {
  func testALineOfArgonLinksBecomesButtons() {
    let blocks = ArgonMarkdown.blocks(
      "[Start HW 9](argon:start/abc123) [Done](argon:complete/abc123)", messageID: "m")

    guard case .actions(let row) = blocks.first else {
      return XCTFail("expected buttons, got \(blocks)")
    }
    XCTAssertEqual(row.count, 2)
    XCTAssertEqual(row[0], ArgonAction(label: "Start HW 9", url: "argon:start/abc123"))
    XCTAssertEqual(row[1].verb, "complete")
    XCTAssertEqual(row[1].taskID, "abc123")
  }

  func testASentenceContainingALinkStaysProse() {
    // Otherwise a message ending in a link would lose its sentence.
    let line = "Ready when you are — [Start](argon:start/abc123)"
    XCTAssertEqual(ArgonMarkdown.blocks(line, messageID: "m"), [.paragraph(line)])
  }

  func testAnOrdinaryLinkIsNotAButton() {
    let line = "[Classroom](https://classroom.google.com)"
    XCTAssertEqual(ArgonMarkdown.blocks(line, messageID: "m"), [.paragraph(line)])
  }

  func testAMalformedArgonLinkIsNotAnAction() {
    // Better to render the raw text than to build a button that acts on nothing.
    XCTAssertNil(ArgonAction(label: "x", url: "argon:start"))
    XCTAssertNil(ArgonAction(label: "x", url: "argon:/abc"))
    XCTAssertNil(ArgonAction(label: "x", url: "argon:start/"))
  }

  // MARK: which task a button acts on

  private func board(_ json: String) throws -> [ArgonTask] {
    try JSONDecoder().decode([ArgonTask].self, from: Data(json.utf8))
  }

  private func action(_ url: String) -> ArgonAction {
    ArgonAction(label: "x", url: url)!
  }

  func testAnExactIdFindsItsTask() throws {
    let tasks = try board(#"[{"id":"8c45122ea6b5","title":"HW 9"}]"#)

    XCTAssertEqual(action("argon:start/8c45122ea6b5").target(in: tasks)?.title, "HW 9")
    XCTAssertEqual(action("argon:complete/8c45122ea6b5").target(in: tasks)?.title, "HW 9")
  }

  func testATruncatedIdStillFindsItsTask() throws {
    // The model copies ids out of tool output and sometimes clips them.
    let tasks = try board(#"[{"id":"8c45122ea6b542c6","title":"HW 9"},{"id":"ffff","title":"Chem"}]"#)

    XCTAssertEqual(action("argon:start/8c45122e").target(in: tasks)?.title, "HW 9")
  }

  func testAnAmbiguousPrefixActsOnNothing() throws {
    // Guessing between two tasks is how the wrong assignment gets started.
    let tasks = try board(#"[{"id":"8c45aa","title":"HW 9"},{"id":"8c45bb","title":"Chem"}]"#)

    XCTAssertNil(action("argon:complete/8c45").target(in: tasks))
  }

  func testAnExactIdBeatsALongerIdItPrefixes() throws {
    let tasks = try board(#"[{"id":"abcdef","title":"long"},{"id":"abc","title":"short"}]"#)

    XCTAssertEqual(action("argon:complete/abc").target(in: tasks)?.title, "short")
  }

  func testAButtonThatWouldChangeNothingIsDead() throws {
    let tasks = try board(#"""
      [{"id":"done1","title":"a","done":true},
       {"id":"run1","title":"b","started_at":"2026-09-22T16:00:00"}]
      """#)

    XCTAssertNil(action("argon:start/missing").target(in: tasks), "no such task")
    XCTAssertNil(action("argon:complete/done1").target(in: tasks), "already done")
    XCTAssertNil(action("argon:start/run1").target(in: tasks), "already started")
    XCTAssertNotNil(action("argon:complete/run1").target(in: tasks), "started can finish")
    XCTAssertNil(action("argon:delete/run1").target(in: tasks), "unknown verb")
  }

  func testV1sDoneVerbStillCompletes() throws {
    let tasks = try board(#"[{"id":"t1","title":"a"}]"#)

    XCTAssertNotNil(action("argon:done/t1").target(in: tasks))
    XCTAssertNotNil(action("argon:Complete/t1").target(in: tasks))
  }
}
