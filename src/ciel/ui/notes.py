"""The Instrument has a blank page for a thought caught in passing.

**Native and small.** This AppKit child sits above ordinary windows, takes
keyboard focus on demand, and returns it on Escape or a successful save. The
Chart's Instrument tokens (``remote/chart.html``, 2026-09-08) supply the dark
ground, cyan structure, gold invitation, Menlo labels, and cut corners.

**Writing is not chatting.** Enter makes a paragraph; Shift–Enter submits the
owner's exact text. Until a positive memory receipt arrives the draft stays
on disk, including through a failed connection or a source reload. Errors
stay beside the editor and never substitute an empty page for an unsaved idea.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any

import objc
import AppKit as A
from Foundation import NSObject, NSTimer
from Quartz import CAShapeLayer

from ciel.notes import Draft


def color(value: str, alpha: float = 1.0) -> Any:
    return A.NSColor.colorWithCalibratedRed_green_blue_alpha_(
        *(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)), alpha
    )


class CapturePanel(A.NSPanel):
    def canBecomeKeyWindow(self) -> bool:
        return True

    def canBecomeMainWindow(self) -> bool:
        return True

    def cancelOperation_(self, sender: Any) -> None:
        self.owner.hide_(sender)


class SaveButton(A.NSButton):
    def drawRect_(self, rect: Any) -> None:
        w, h = self.bounds().size
        path = A.NSBezierPath.bezierPath()
        points = ((1, 7), (1, h - 1), (w - 7, h - 1), (w - 1, h - 7), (w - 1, 1), (7, 1))
        if self.isFlipped():
            points = tuple((x, h - y) for x, y in points)
        path.moveToPoint_(points[0])
        for point in points[1:]:
            path.lineToPoint_(point)
        path.closePath()
        enabled = self.isEnabled()
        color("e8c98a", 0.12 if enabled else 0.04).setFill()
        path.fill()
        focused = self.window() is not None and self.window().firstResponder() == self
        color("a0f0ff" if focused else "e8c98a", 0.8 if focused else 0.45 if enabled else 0.16).setStroke()
        path.setLineWidth_(1)
        path.stroke()
        label = A.NSAttributedString.alloc().initWithString_attributes_("SAVE  ⇧↵", {
            A.NSFontAttributeName: A.NSFont.fontWithName_size_("Menlo", 11),
            A.NSForegroundColorAttributeName: color("e8c98a", 1.0 if enabled else 0.35),
        })
        size = label.size()
        label.drawAtPoint_(((w - size.width) / 2, (h - size.height) / 2))


class NoteText(A.NSTextView):
    def keyDown_(self, event: Any) -> None:
        mask = (A.NSEventModifierFlagShift | A.NSEventModifierFlagCommand |
                A.NSEventModifierFlagControl | A.NSEventModifierFlagOption)
        flags = event.modifierFlags() & mask
        if not self.hasMarkedText():
            if event.keyCode() == 48 and flags in (0, A.NSEventModifierFlagShift):
                if flags:
                    self.window().selectPreviousKeyView_(self)
                else:
                    self.window().selectNextKeyView_(self)
                return
            if event.keyCode() in (36, 76) and flags == A.NSEventModifierFlagShift:
                if not event.isARepeat():
                    self.owner.save_(None)
                return
            if event.keyCode() == 53 and not flags:
                self.owner.hide_(None)
                return
        objc.super(NoteText, self).keyDown_(event)


class NoteController(NSObject):
    def initWithConfig_(self, config: dict[str, Any]) -> Any:
        self = objc.super(NoteController, self).init()
        if self is None:
            return None
        self.max_chars = int(config.get("max_chars", 16000))
        self.saving = False
        self.previous_app = None
        self.saved_timer = None
        self.draft_error = False
        path = Path(config["dir"]) / "draft.json"
        try:
            self.draft = Draft.load(path)
        except (OSError, ValueError, TypeError):
            self.draft = Draft(path)
            self.draft_error = True
        self.build()
        if self.draft_error:
            self.status("The existing draft could not be read. It has been left untouched.", error=True)
            self.editor.setEditable_(False)
            self.save_button.setEnabled_(False)
        return self

    @objc.python_method
    def label(self, text: str, frame: Any, size: float, tint: str, *, serif: bool = False) -> Any:
        label = A.NSTextField.labelWithString_(text)
        label.setFrame_(frame)
        font = A.NSFont.fontWithName_size_("Georgia" if serif else "Menlo", size)
        label.setFont_(font or A.NSFont.systemFontOfSize_(size))
        label.setTextColor_(color(tint))
        self.container.addSubview_(label)
        return label

    @objc.python_method
    def build(self) -> None:
        visible = A.NSScreen.mainScreen().visibleFrame()
        w, h = min(620.0, visible.size.width - 40), min(390.0, visible.size.height - 40)
        frame = A.NSMakeRect(0, 0, w, h)
        self.window = CapturePanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, A.NSWindowStyleMaskBorderless, A.NSBackingStoreBuffered, False
        )
        self.window.owner = self
        self.window.setTitle_("Ciel — Quick note")
        self.window.setLevel_(A.NSFloatingWindowLevel)
        self.window.setCollectionBehavior_(A.NSWindowCollectionBehaviorCanJoinAllSpaces | A.NSWindowCollectionBehaviorFullScreenAuxiliary)
        self.window.setHidesOnDeactivate_(False)
        self.window.setReleasedWhenClosed_(False)
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(A.NSColor.clearColor())
        self.window.setHasShadow_(True)
        self.window.setMovableByWindowBackground_(True)
        self.container = A.NSView.alloc().initWithFrame_(frame)
        self.container.setWantsLayer_(True)
        self.container.layer().setBackgroundColor_(color("0a1620").CGColor())
        self.window.setContentView_(self.container)
        path = A.NSBezierPath.bezierPath()
        path.moveToPoint_((0, 12))
        for point in ((0, h), (w - 12, h), (w, h - 12), (w, 0), (12, 0)):
            path.lineToPoint_(point)
        path.closePath()
        mask = CAShapeLayer.layer()
        mask.setPath_(path.CGPath())
        self.container.layer().setMask_(mask)
        border = CAShapeLayer.layer()
        border.setPath_(path.CGPath())
        border.setFillColor_(A.NSColor.clearColor().CGColor())
        border.setStrokeColor_(color("a0f0ff", 0.28).CGColor())
        border.setLineWidth_(2)
        self.container.layer().addSublayer_(border)
        self.label("CIEL  /  MEMORY", A.NSMakeRect(26, h - 44, w - 100, 18), 11, "e8c98a")
        self.label("Catch a thought.", A.NSMakeRect(26, h - 91, w - 52, 35), 27, "dfe6e6", serif=True)
        self.close_button = A.NSButton.buttonWithTitle_target_action_("×", self, "hide:")
        self.close_button.setFrame_(A.NSMakeRect(w - 53, h - 50, 30, 30))
        self.close_button.setBezelStyle_(A.NSBezelStyleInline)
        self.close_button.setToolTip_("Hide note · Escape · draft kept")
        self.container.addSubview_(self.close_button)
        self.scroll = A.NSScrollView.alloc().initWithFrame_(A.NSMakeRect(26, 102, w - 52, h - 213))
        self.scroll.setHasVerticalScroller_(True)
        self.scroll.setAutohidesScrollers_(True)
        self.scroll.setDrawsBackground_(False)
        content_width = self.scroll.contentSize().width
        self.editor = NoteText.alloc().initWithFrame_(A.NSMakeRect(0, 0, content_width, h - 213))
        self.editor.owner = self
        self.editor.setRichText_(False)
        self.editor.setImportsGraphics_(False)
        self.editor.setDrawsBackground_(False)
        self.editor.setTextColor_(color("dfe6e6"))
        self.editor.setInsertionPointColor_(color("a0f0ff"))
        self.editor.setFont_(A.NSFont.systemFontOfSize_(16))
        self.editor.setTextContainerInset_((0, 6))
        self.editor.setVerticallyResizable_(True)
        self.editor.setHorizontallyResizable_(False)
        self.editor.setMaxSize_((content_width, 1000000))
        self.editor.textContainer().setContainerSize_((content_width, 1000000))
        self.editor.textContainer().setWidthTracksTextView_(True)
        self.editor.setAutoresizingMask_(A.NSViewWidthSizable)
        self.editor.setAutomaticQuoteSubstitutionEnabled_(False)
        self.editor.setAutomaticDashSubstitutionEnabled_(False)
        self.editor.setAutomaticSpellingCorrectionEnabled_(False)
        self.editor.setString_(self.draft.text)
        self.editor.setDelegate_(self)
        self.scroll.setDocumentView_(self.editor)
        self.container.addSubview_(self.scroll)
        self.placeholder = self.label("An idea, a line, something to come back to…", A.NSMakeRect(31, h - 147, w - 62, 25), 13, "8fa8b3")
        self.placeholder.setHidden_(bool(self.draft.text))
        self.message = self.label("", A.NSMakeRect(26, 61, w - 52, 34), 10, "8fa8b3")
        self.message.setMaximumNumberOfLines_(2)
        self.label("ESC  tuck away", A.NSMakeRect(26, 25, w - 220, 20), 10, "8fa8b3")
        self.save_button = SaveButton.buttonWithTitle_target_action_("Save  ⇧↵", self, "save:")
        self.save_button.setFrame_(A.NSMakeRect(w - 174, 18, 148, 32))
        self.save_button.setBordered_(False)
        self.save_button.setContentTintColor_(color("e8c98a"))
        self.container.addSubview_(self.save_button)
        self.editor.setNextKeyView_(self.save_button)
        self.save_button.setNextKeyView_(self.close_button)
        self.close_button.setNextKeyView_(self.editor)
        self.window.center()
        self.refresh()

    @objc.python_method
    def status(self, text: str, *, error: bool = False) -> None:
        self.message.setStringValue_(text)
        self.message.setTextColor_(color("e88a8a" if error else "8fa8b3"))
        self.message.setToolTip_(text)

    @objc.python_method
    def refresh(self) -> None:
        text = self.editor.string()
        self.placeholder.setHidden_(bool(text))
        self.save_button.setEnabled_(bool(text.strip()) and len(text) <= self.max_chars and not self.saving and not self.draft_error)
        if not self.saving:
            self.status(f"{len(text):,} / {self.max_chars:,}  ·  Shift–Enter saves to memory", error=len(text) > self.max_chars)

    def textDidChange_(self, notification: Any) -> None:
        self.refresh()
        try:
            self.draft.update(self.editor.string())
        except OSError:
            self.status("Draft could not be kept on disk. Keep this window open and retry saving.", error=True)

    def save_(self, sender: Any) -> None:
        text = self.editor.string()
        if self.saving or self.draft_error:
            return
        if not text.strip() or len(text) > self.max_chars:
            self.status("Write a note within the length limit first.", error=True)
            return
        try:
            self.draft.update(text)
        except OSError:
            self.status("The draft could not be written. Keep this window open and try again.", error=True)
            return
        self.saving = True
        self.editor.setEditable_(False)
        self.save_button.setEnabled_(False)
        self.status("Saving to memory…")
        print(json.dumps({"type": "note.save", "note_id": self.draft.note_id, "text": text}), flush=True)

    def hide_(self, sender: Any) -> None:
        if not self.draft_error and self.editor.string() and self.editor.string() != self.draft.text:
            try:
                self.draft.update(self.editor.string())
            except OSError:
                self.status("The draft is not on disk yet. Keep this window open and retry.", error=True)
                return
        self.window.orderOut_(None)
        if self.previous_app is not None:
            self.previous_app.activateWithOptions_(A.NSApplicationActivateIgnoringOtherApps)
            self.previous_app = None

    def savedHide_(self, timer: Any) -> None:
        if not self.editor.string() and not self.saving:
            self.hide_(None)
        self.saved_timer = None

    def handleLine_(self, line: str) -> None:
        frame = json.loads(line)
        if frame["type"] == "quit":
            A.NSApplication.sharedApplication().terminate_(None)
        elif frame["type"] == "show":
            if self.saved_timer is not None:
                self.saved_timer.invalidate()
                self.saved_timer = None
            if not self.window.isVisible():
                self.previous_app = A.NSWorkspace.sharedWorkspace().frontmostApplication()
                self.window.center()
            A.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            self.window.makeKeyAndOrderFront_(None)
            self.window.makeFirstResponder_(self.editor)
        elif frame["type"] == "note.result" and frame.get("note_id") == self.draft.note_id:
            self.saving = False
            self.editor.setEditable_(True)
            try:
                saved = self.draft.receipt(frame)
            except OSError:
                self.refresh()
                self.status("Saved to memory; the local draft could not be cleared. Retrying is safe.", error=True)
                return
            if saved:
                self.editor.setString_("")
                self.refresh()
                self.status("Saved to memory.")
                self.message.setTextColor_(color("7adea8"))
                self.saved_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(0.65, self, "savedHide:", None, False)
            else:
                self.refresh()
                self.status(frame.get("error", "No save receipt. Your draft is still here."), error=True)


def pump(controller: NoteController) -> None:
    for line in sys.stdin:
        controller.performSelectorOnMainThread_withObject_waitUntilDone_("handleLine:", line, False)
    controller.performSelectorOnMainThread_withObject_waitUntilDone_("handleLine:", '{"type":"quit"}', False)


def main() -> int:
    app = A.NSApplication.sharedApplication()
    app.setActivationPolicy_(A.NSApplicationActivationPolicyAccessory)
    controller = NoteController.alloc().initWithConfig_(json.loads(sys.argv[1]))
    threading.Thread(target=pump, args=(controller,), daemon=True).start()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
