"""The Instrument has a blank page for a thought caught in passing.

**A bar until the thought needs more room.** The owner's Quick Note Prototype
(2026-09-09) replaces the large sheet with a 560-point bar: a gold dot, one
line that grows to 180 points, and controls revealed by hover or the keyboard.
The Chart's Instrument tokens (``remote/chart.html``) remain the source for
colour and typography. Saving folds to one line; a receipt turns it green.
This AppKit child takes focus on demand and remembers where it was dragged.

**Writing is not chatting.** Enter makes a paragraph; Shift–Enter submits the
owner's exact text. Until a positive memory receipt or explicit dismissal,
the draft stays on disk through a failed connection or source reload. Errors
stay beside the editor and never substitute an empty page for an unsaved idea.

**More when asked.** Hover and keyboard focus expose Recent, Context, and
Dictate. History never replaces the current draft; context is visible text
read only on request; dictation returns to the editor before Save. Discard
deletes the disk draft immediately and offers a brief undo held only in RAM.
"""
from __future__ import annotations

import json
import math
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import objc
import AppKit as A
from Foundation import NSObject, NSTimer
from Quartz import CABasicAnimation, CALayer, CAShapeLayer, CATransaction

from ciel.notes import Draft
from ciel.ui.note_context import capture_context


def color(value: str, alpha: float = 1.0) -> Any:
    # CSS hex tokens are sRGB; calibrated RGB visibly lifts the dark ground.
    return A.NSColor.colorWithSRGBRed_green_blue_alpha_(
        *(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)), alpha
    )


class CapturePanel(A.NSPanel):
    def canBecomeKeyWindow(self) -> bool:
        return True

    def canBecomeMainWindow(self) -> bool:
        return True

    def cancelOperation_(self, sender: Any) -> None:
        self.owner.hide_(sender)


class HistoryPanel(CapturePanel):
    def cancelOperation_(self, sender: Any) -> None:
        self.owner.closeHistory_(sender)

    def performKeyEquivalent_(self, event: Any) -> bool:
        editor = self.firstResponder()
        flags = event.modifierFlags() & (A.NSEventModifierFlagCommand | A.NSEventModifierFlagOption | A.NSEventModifierFlagControl | A.NSEventModifierFlagShift)
        if isinstance(editor, A.NSTextView) and editor.isFieldEditor() and flags == A.NSEventModifierFlagCommand:
            key = (event.charactersIgnoringModifiers() or '').lower()
            action = {'a': editor.selectAll_, 'x': editor.cut_, 'c': editor.copy_, 'v': editor.paste_}.get(key)
            if action is not None:
                action(self)
                return True
        return objc.super(HistoryPanel, self).performKeyEquivalent_(event)


class HistoryText(A.NSTextView):
    def performKeyEquivalent_(self, event: Any) -> bool:
        flags = event.modifierFlags() & (A.NSEventModifierFlagCommand | A.NSEventModifierFlagOption | A.NSEventModifierFlagControl | A.NSEventModifierFlagShift)
        if self.window().firstResponder() == self and flags == A.NSEventModifierFlagCommand:
            key = (event.charactersIgnoringModifiers() or '').lower()
            if key in ('a', 'c'):
                (self.selectAll_ if key == 'a' else self.copy_)(self)
                return True
        return objc.super(HistoryText, self).performKeyEquivalent_(event)


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
        label = A.NSAttributedString.alloc().initWithString_attributes_(self.title(), {
            A.NSFontAttributeName: A.NSFont.fontWithName_size_("Menlo", 11),
            A.NSForegroundColorAttributeName: color("e8c98a", 1.0 if enabled else 0.35),
        })
        size = label.size()
        label.drawAtPoint_(((w - size.width) / 2, (h - size.height) / 2))


class NoteText(A.NSTextView):
    def performKeyEquivalent_(self, event: Any) -> bool:
        # This accessory has no Edit menu to supply the usual text shortcuts.
        mask = (A.NSEventModifierFlagShift | A.NSEventModifierFlagCommand |
                A.NSEventModifierFlagControl | A.NSEventModifierFlagOption)
        window = self.window()
        if (window is not None and window.firstResponder() == self and
                self.isEditable() and not self.hasMarkedText() and
                event.modifierFlags() & mask == A.NSEventModifierFlagCommand):
            action = {"a": self.selectAll_, "x": self.cut_, "c": self.copy_, "v": self.paste_}.get(
                (event.charactersIgnoringModifiers() or "").lower()
            )
            if action is not None:
                action(self)
                return True
        return objc.super(NoteText, self).performKeyEquivalent_(event)

    def keyDown_(self, event: Any) -> None:
        mask = (A.NSEventModifierFlagShift | A.NSEventModifierFlagCommand |
                A.NSEventModifierFlagControl | A.NSEventModifierFlagOption)
        flags = event.modifierFlags() & mask
        if not self.hasMarkedText():
            if event.keyCode() == 48 and flags in (0, A.NSEventModifierFlagShift):
                self.owner.keyboard_footer = True
                self.owner.layout()
                target = self.owner.close_button if flags or not self.owner.save_button.isEnabled() else self.owner.save_button
                self.window().makeFirstResponder_(target)
                return
            if event.keyCode() in (36, 76) and flags == A.NSEventModifierFlagShift:
                if not event.isARepeat():
                    self.owner.save_(None)
                return
            if event.keyCode() == 53 and not flags:
                self.owner.hide_(None)
                return
        objc.super(NoteText, self).keyDown_(event)


class NoteSurface(A.NSView):
    def updateTrackingAreas(self) -> None:
        objc.super(NoteSurface, self).updateTrackingAreas()
        for area in self.trackingAreas():
            self.removeTrackingArea_(area)
        area = A.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), A.NSTrackingMouseEnteredAndExited | A.NSTrackingActiveAlways | A.NSTrackingInVisibleRect,
            self, None,
        )
        self.addTrackingArea_(area)

    def mouseEntered_(self, event: Any) -> None:
        self.owner.hover = True
        self.owner.layout()

    def mouseExited_(self, event: Any) -> None:
        self.owner.hover = False
        self.owner.layout()

    def mouseDown_(self, event: Any) -> None:
        self.window().performWindowDragWithEvent_(event)


class NoteController(NSObject):
    def initWithConfig_(self, config: dict[str, Any]) -> Any:
        self = objc.super(NoteController, self).init()
        if self is None:
            return None
        self.max_chars = int(config.get("max_chars", 16000))
        self.saving = False
        self.saved = False
        self.error_text = ""
        self.notice_text = ''
        self.hover = False
        self.keyboard_footer = False
        self.previous_app = None
        self.saved_timer = None
        self.saved_origin = None
        self.saved_started = 0.0
        self.saved_reduce_motion = False
        self.undo_seconds = max(0.0, min(float(config.get('undo_discard_s', 5.0)), 30.0))
        self.undo_text = ''
        self.undo_context = ''
        self.undo_deadline = 0.0
        self.undo_timer = None
        self.undo_panel = None
        self.history_window = None
        self.history_items = []
        self.context_generation = 0
        self.context_busy = False
        self.context_text = ''
        self.source_app = ('', '')
        self.dictation_session = ''
        self.dictation_state = ''
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
    def label(self, text: str, size: float, tint: str, *, serif: bool = False, sans: bool = False) -> Any:
        label = A.NSTextField.labelWithString_(text)
        font = A.NSFont.systemFontOfSize_(size) if sans else A.NSFont.fontWithName_size_("Georgia" if serif else "Menlo", size)
        label.setFont_(font or A.NSFont.systemFontOfSize_(size))
        label.setTextColor_(color(tint))
        self.container.addSubview_(label)
        return label

    @objc.python_method
    def build(self) -> None:
        visible = A.NSScreen.mainScreen().visibleFrame()
        self.width = min(560.0, visible.size.width - 40)
        frame = A.NSMakeRect(0, 0, self.width, 59)
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
        self.container = NoteSurface.alloc().initWithFrame_(frame)
        self.container.owner = self
        self.container.setWantsLayer_(True)
        self.container.layer().setBackgroundColor_(color("0a1620").CGColor())
        self.window.setContentView_(self.container)
        self.mask = CAShapeLayer.layer()
        self.container.layer().setMask_(self.mask)
        self.border = CAShapeLayer.layer()
        self.border.setFillColor_(A.NSColor.clearColor().CGColor())
        self.border.setLineWidth_(2)
        self.container.layer().addSublayer_(self.border)
        self.dot = CALayer.layer()
        self.dot.setCornerRadius_(3.5)
        self.dot.setShadowOffset_((0, 0))
        self.dot.setShadowRadius_(4.5)
        self.dot.setShadowOpacity_(0.6)
        self.container.layer().addSublayer_(self.dot)
        self._dot_breathing = False

        self.scroll = A.NSScrollView.alloc().initWithFrame_(A.NSMakeRect(37, 17, self.width - 56, 25))
        self.scroll.setHasVerticalScroller_(True)
        self.scroll.setAutohidesScrollers_(True)
        self.scroll.setScrollerStyle_(A.NSScrollerStyleOverlay)
        self.scroll.setDrawsBackground_(False)
        self.editor = NoteText.alloc().initWithFrame_(A.NSMakeRect(0, 0, self.width - 56, 25))
        self.editor.owner = self
        self.editor.setRichText_(False)
        self.editor.setImportsGraphics_(False)
        self.editor.setDrawsBackground_(False)
        self.editor.setTextColor_(color("dfe6e6"))
        self.editor.setInsertionPointColor_(color("a0f0ff"))
        self.editor.setFont_(A.NSFont.systemFontOfSize_(17))
        paragraph = A.NSMutableParagraphStyle.alloc().init()
        paragraph.setMinimumLineHeight_(24.65)
        paragraph.setMaximumLineHeight_(24.65)
        self.paragraph = paragraph
        self.editor.setDefaultParagraphStyle_(paragraph)
        self.editor.setTextContainerInset_((0, 0))
        self.editor.textContainer().setLineFragmentPadding_(0)
        self.editor.setVerticallyResizable_(True)
        self.editor.setHorizontallyResizable_(False)
        self.editor.setMaxSize_((self.width, 1000000))
        self.editor.textContainer().setWidthTracksTextView_(True)
        self.editor.setAutoresizingMask_(A.NSViewWidthSizable)
        self.editor.setAutomaticQuoteSubstitutionEnabled_(False)
        self.editor.setAutomaticDashSubstitutionEnabled_(False)
        self.editor.setAutomaticSpellingCorrectionEnabled_(False)
        self.editor.setString_(self.draft.text)
        self.editor.setDelegate_(self)
        self.editor.setAccessibilityLabel_("Quick note")
        self.scroll.setDocumentView_(self.editor)
        self.container.addSubview_(self.scroll)
        self.placeholder = self.label("An idea, a line, something to come back to…", 17, "8fa8b3", sans=True)
        self.placeholder.setLineBreakMode_(A.NSLineBreakByTruncatingTail)
        self.top_hint = self.label("⇧↵ SAVE", 10, "e8c98a")
        self.top_hint.setTextColor_(color("e8c98a", 0.55))
        self.summary = self.label("", 17, "dfe6e6", sans=True)
        self.summary.setLineBreakMode_(A.NSLineBreakByTruncatingTail)
        self.state_label = self.label("", 10, "8fa8b3")
        self.message = self.label("", 10.5, "e88a8a")
        self.message.setMaximumNumberOfLines_(0)
        self.message.setLineBreakMode_(A.NSLineBreakByWordWrapping)
        self.message.cell().setUsesSingleLineMode_(False)
        self.message.cell().setWraps_(True)
        self.footer = A.NSView.alloc().initWithFrame_(A.NSZeroRect)
        self.container.addSubview_(self.footer)
        self.separator = CALayer.layer()
        self.separator.setBackgroundColor_(color("a0f0ff", 0.14).CGColor())
        self.container.layer().addSublayer_(self.separator)
        self.count = self.label("", 10, "8fa8b3")
        self.count.removeFromSuperview()
        self.footer.addSubview_(self.count)
        self.close_button = A.NSButton.buttonWithTitle_target_action_("ESC DISCARD", self, "hide:")
        self.close_button.setBordered_(False)
        self.close_button.setFont_(A.NSFont.fontWithName_size_("Menlo", 10))
        self.close_button.setContentTintColor_(color("8fa8b3"))
        self.close_button.setToolTip_("Discard this draft and close · Escape")
        self.footer.addSubview_(self.close_button)
        self.save_button = SaveButton.buttonWithTitle_target_action_("SAVE  ⇧↵", self, "save:")
        self.save_button.setBordered_(False)
        self.save_button.setAccessibilityLabel_("Save note")
        self.footer.addSubview_(self.save_button)
        self.recent_button = self.utility_button('RECENT', 'recent:', 'Browse confirmed notes saved on this Mac')
        self.context_button = self.utility_button('+ CONTEXT', 'context:', 'Attach the app or page you came from')
        self.dictate_button = self.utility_button('DICTATE', 'dictate:', 'Speak into this draft; click again to stop')
        self.editor.setNextKeyView_(self.save_button)
        self.save_button.setNextKeyView_(self.close_button)
        self.close_button.setNextKeyView_(self.recent_button)
        self.recent_button.setNextKeyView_(self.context_button)
        self.context_button.setNextKeyView_(self.dictate_button)
        self.dictate_button.setNextKeyView_(self.editor)
        self.refresh()
        h = self.window.frame().size.height
        self.window.setFrameOrigin_((visible.origin.x + (visible.size.width - self.width) / 2,
                                     visible.origin.y + visible.size.height * 0.72 - h))

    @objc.python_method
    def utility_button(self, title: str, action: str, hint: str) -> Any:
        button = A.NSButton.buttonWithTitle_target_action_(title, self, action)
        button.setBordered_(False)
        button.setFont_(A.NSFont.fontWithName_size_('Menlo', 10))
        button.setContentTintColor_(color('8fa8b3'))
        button.setToolTip_(hint)
        self.footer.addSubview_(button)
        return button

    @objc.python_method
    def layout(self) -> None:
        editing = not self.saving and not self.saved
        focused = self.window.firstResponder() in (self.save_button, self.close_button, self.recent_button, self.context_button, self.dictate_button)
        footer = editing and (self.hover or self.keyboard_footer or focused or bool(self.error_text or self.notice_text) or bool(self.dictation_session) or self.context_busy)
        hint = editing and not self.editor.string() and not footer
        w = self.width
        text_width = w - 56 - (82 if hint else 0)
        self.editor.setFrameSize_((text_width, self.editor.frame().size.height))
        self.editor.textContainer().setContainerSize_((text_width, 1000000))
        storage = self.editor.textStorage()
        storage.addAttribute_value_range_(A.NSParagraphStyleAttributeName, self.paragraph, (0, storage.length()))
        manager = self.editor.layoutManager()
        manager.ensureLayoutForTextContainer_(self.editor.textContainer())
        used = manager.usedRectForTextContainer_(self.editor.textContainer())
        extra = manager.extraLineFragmentRect()
        text_height = math.ceil(max(25, used.size.height, extra.origin.y + extra.size.height))
        self.editor.setFrameSize_((text_width, text_height))
        row_height = min(180, text_height) if editing else 25
        footer_height = (97 if w < 440 else 79) if footer else 0
        error_height = 0
        if editing and (self.error_text or self.notice_text):
            self.message.setStringValue_(self.error_text or self.notice_text)
            self.message.setTextColor_(color('e88a8a' if self.error_text else '8fa8b3'))
            bounds = self.message.attributedStringValue().boundingRectWithSize_options_(
                (w - 56, 1000), A.NSStringDrawingUsesLineFragmentOrigin | A.NSStringDrawingUsesFontLeading
            )
            error_height = math.ceil(bounds.size.height) + 12
        h = 34 + row_height + error_height + footer_height
        old = self.window.frame()
        self.window.setFrame_display_(A.NSMakeRect(old.origin.x, old.origin.y + old.size.height - h, w, h), True)
        self.container.setFrame_(A.NSMakeRect(0, 0, w, h))
        y = h - 17 - row_height
        self.scroll.setFrame_(A.NSMakeRect(37, y, text_width, row_height))
        self.scroll.setHidden_(not editing)
        self.placeholder.setFrame_(A.NSMakeRect(37, h - 42, text_width, 25))
        self.placeholder.setStringValue_({'starting': 'Starting dictation…', 'listening': 'Listening… click Stop when done.', 'transcribing': 'Turning speech into text…'}.get(self.dictation_state, 'An idea, a line, something to come back to…'))
        self.placeholder.setHidden_(not editing or bool(self.editor.string()))
        self.top_hint.setFrame_(A.NSMakeRect(w - 87, h - 40, 68, 22))
        self.top_hint.setHidden_(not hint)
        self.summary.setHidden_(editing)
        self.state_label.setHidden_(editing)
        if not editing:
            self.summary.setStringValue_("Saved to memory." if self.saved else " ".join(self.editor.string().splitlines()))
            self.summary.setFont_(A.NSFont.fontWithName_size_("Georgia", 16) if self.saved else A.NSFont.systemFontOfSize_(17))
            self.summary.setTextColor_(color("a6ecc7") if self.saved else color("dfe6e6", 0.55))
            self.state_label.setStringValue_("TUCKING AWAY" if self.saved else "SAVING TO MEMORY…")
            self.state_label.setTextColor_(color("7adea8", 0.6) if self.saved else color("8fa8b3"))
            state_width = 102 if self.saved else 135
            self.summary.setFrame_(A.NSMakeRect(37, h - 42, w - 70 - state_width, 25))
            self.state_label.setFrame_(A.NSMakeRect(w - 19 - state_width, h - 40, state_width, 22))
        self.message.setFrame_(A.NSMakeRect(37, footer_height + 12, w - 56, max(0, error_height - 12)))
        self.message.setHidden_(not editing or not (self.error_text or self.notice_text))
        self.footer.setFrame_(A.NSMakeRect(0, 0, w, footer_height))
        self.footer.setHidden_(not footer)
        self.separator.setHidden_(not footer)
        self.separator.setFrame_(A.NSMakeRect(1, footer_height, w - 2, 1))
        self.count.setStringValue_(f"{len(self.editor.string()):,} / {self.max_chars:,}")
        self.count.setFrame_(A.NSMakeRect(37, 42 if w < 440 else 17, 120, 15))
        self.close_button.setFrame_(A.NSMakeRect(w - 264, 10, 124, 27))
        self.save_button.setFrame_(A.NSMakeRect(w - 128, 10, 109, 27))
        self.recent_button.setFrame_(A.NSMakeRect(30, footer_height - 28, 70, 22))
        self.context_button.setFrame_(A.NSMakeRect(108, footer_height - 28, 132, 22))
        self.dictate_button.setFrame_(A.NSMakeRect(w - 140, footer_height - 28, 121, 22))
        self.context_button.setTitle_('READING…' if self.context_busy else 'REMOVE CONTEXT' if self.context_text else '+ CONTEXT')
        self.context_button.setEnabled_(not self.context_busy and not self.dictation_session and not self.draft_error)
        self.dictate_button.setTitle_({'starting': 'STARTING…', 'listening': 'STOP DICTATION', 'transcribing': 'TRANSCRIBING…'}.get(self.dictation_state, 'DICTATE'))
        self.dictate_button.setEnabled_(not self.context_busy and self.dictation_state in ('', 'listening') and not self.draft_error)
        self.save_button.setTitle_("RETRY  ⇧↵" if self.error_text else "SAVE  ⇧↵")
        self.save_button.setNeedsDisplay_(True)
        tone = "e88a8a" if self.error_text else "7adea8" if self.saved else "e8c98a"
        path = A.NSBezierPath.bezierPath()
        path.moveToPoint_((0, 10))
        for point in ((0, h), (w - 10, h), (w, h - 10), (w, 0), (10, 0)):
            path.lineToPoint_(point)
        path.closePath()
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        self.mask.setPath_(path.CGPath())
        self.border.setPath_(path.CGPath())
        self.border.setStrokeColor_(color(tone if self.error_text or self.saved else "a0f0ff", 0.6 if self.error_text else 0.55 if self.saved else 0.28).CGColor())
        self.dot.setFrame_(A.NSMakeRect(17, h - 31, 7, 7))
        self.dot.setBackgroundColor_(color(tone).CGColor())
        self.dot.setShadowColor_(color(tone).CGColor())
        CATransaction.commit()
        breathing = self.saving or bool(self.dictation_session)
        if breathing != self._dot_breathing:
            self._dot_breathing = breathing
            self.dot.removeAnimationForKey_("saving")
            if breathing:
                animation = CABasicAnimation.animationWithKeyPath_("opacity")
                animation.setFromValue_(0.45)
                animation.setToValue_(1.0)
                animation.setDuration_(0.8)
                animation.setAutoreverses_(True)
                animation.setRepeatCount_(1e9)
                self.dot.addAnimation_forKey_(animation, "saving")

    @objc.python_method
    def status(self, text: str, *, error: bool = False) -> None:
        self.notice_text = ''
        self.message.setStringValue_(text)
        self.message.setToolTip_(text)
        self.message.setTextColor_(color("e88a8a"))
        self.error_text = text if error else ""
        self.layout()

    @objc.python_method
    def refresh(self) -> None:
        text = self.editor.string()
        self.save_button.setEnabled_(bool(text.strip()) and len(text) <= self.max_chars and not self.saving and not self.draft_error and not self.context_busy and not self.dictation_session)
        if len(text) > self.max_chars:
            self.error_text = "This note exceeds the length limit. Shorten it before saving."
        self.layout()

    def textDidChange_(self, notification: Any) -> None:
        self.notice_text = ''
        if self.editor.string():
            self.forgetUndo_(None)
        self.saved = False
        self.error_text = ""
        self.keyboard_footer = False
        self.refresh()
        try:
            self.draft.update(self.editor.string())
        except OSError:
            self.status("Draft could not be kept on disk. Keep this window open and retry saving.", error=True)

    @objc.python_method
    def offer_undo(self, text: str, context: str = '') -> None:
        self.forgetUndo_(None)
        self.undo_text, self.undo_deadline = text, time.monotonic() + self.undo_seconds
        self.undo_context = context
        if self.undo_panel is None:
            self.undo_panel = A.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                A.NSMakeRect(0, 0, 270, 42), A.NSWindowStyleMaskBorderless | A.NSWindowStyleMaskNonactivatingPanel,
                A.NSBackingStoreBuffered, False)
            self.undo_panel.setLevel_(A.NSFloatingWindowLevel)
            self.undo_panel.setHidesOnDeactivate_(False)
            self.undo_panel.setBackgroundColor_(color('0a1620'))
            self.undo_panel.setHasShadow_(True)
            self.undo_panel.contentView().setWantsLayer_(True)
            self.undo_panel.contentView().layer().setBackgroundColor_(color('0a1620').CGColor())
            self.undo_panel.contentView().layer().setBorderWidth_(1)
            self.undo_panel.contentView().layer().setBorderColor_(color('a0f0ff', 0.28).CGColor())
            label = A.NSTextField.labelWithString_('Draft discarded')
            label.setTextColor_(color('8fa8b3'))
            label.setFont_(A.NSFont.systemFontOfSize_(12))
            label.setFrame_(A.NSMakeRect(15, 12, 140, 19))
            self.undo_panel.contentView().addSubview_(label)
            button = A.NSButton.buttonWithTitle_target_action_('UNDO', self, 'undoDiscard:')
            button.setBordered_(False)
            button.setContentTintColor_(color('e8c98a'))
            button.setFrame_(A.NSMakeRect(183, 7, 72, 28))
            self.undo_panel.contentView().addSubview_(button)
        frame = self.window.frame()
        screen = self.window.screen() or A.NSScreen.mainScreen()
        bottom = screen.visibleFrame().origin.y
        self.undo_panel.setFrameOrigin_((frame.origin.x, max(bottom + 8, frame.origin.y - 50)))
        self.undo_panel.orderFrontRegardless()
        self.undo_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(self.undo_seconds, self, 'forgetUndo:', None, False)

    def forgetUndo_(self, sender: Any) -> None:
        if self.undo_timer is not None:
            self.undo_timer.invalidate()
        self.undo_timer = None
        self.undo_text = ''
        self.undo_context = ''
        self.undo_deadline = 0.0
        if self.undo_panel is not None:
            self.undo_panel.orderOut_(None)

    def undoDiscard_(self, sender: Any) -> None:
        text = self.undo_text if time.monotonic() < self.undo_deadline else ''
        context = self.undo_context
        self.forgetUndo_(None)
        if text and not self.editor.string() and not self.saving:
            self.handleLine_('{"type":"show"}')
            self.editor.insertText_replacementRange_(text, (0, 0))
            self.context_text = context
            self.refresh()

    def dictate_(self, sender: Any) -> None:
        if self.saving or self.saved or self.context_busy or self.draft_error:
            return
        if self.dictation_session:
            if self.dictation_state == 'listening':
                print(json.dumps({'type': 'note.dictation', 'session': self.dictation_session, 'action': 'stop'}), flush=True)
                self.dictation_state = 'transcribing'
                self.refresh()
            return
        self.dictation_session, self.dictation_state = uuid.uuid4().hex, 'starting'
        if self.context_text and str(self.editor.string()).endswith(self.context_text):
            body = str(self.editor.string())[:-len(self.context_text)]
            self.editor.setSelectedRange_((len(body.encode('utf-16-le')) // 2, 0))
        self.editor.setEditable_(False)
        self.error_text = ''
        self.refresh()
        print(json.dumps({'type': 'note.dictation', 'session': self.dictation_session, 'action': 'start'}), flush=True)

    @objc.python_method
    def cancel_dictation(self) -> None:
        if self.dictation_session:
            print(json.dumps({'type': 'note.dictation', 'session': self.dictation_session, 'action': 'cancel'}), flush=True)
        self.dictation_session = self.dictation_state = ''
        self.editor.setEditable_(not self.draft_error)

    def context_(self, sender: Any) -> None:
        if self.context_busy or self.saving or self.saved or self.dictation_session or self.draft_error:
            return
        if self.context_text:
            text = str(self.editor.string())
            block, self.context_text = self.context_text, ''
            if block in text:
                self.editor.setString_(text.replace(block, '', 1))
                self.textDidChange_(None)
            self.refresh()
            return
        if not self.source_app[0]:
            self.status('No previous app is available to attach.', error=True)
            return
        self.context_busy = True
        self.context_generation += 1
        generation, source = self.context_generation, self.source_app
        self.refresh()

        def read() -> None:
            block, page_missing = capture_context(*source)
            result = json.dumps({'generation': generation, 'text': block, 'page_missing': page_missing})
            self.performSelectorOnMainThread_withObject_waitUntilDone_('contextReady:', result, False)

        threading.Thread(target=read, daemon=True).start()

    def contextReady_(self, payload: str) -> None:
        result = json.loads(payload)
        if result['generation'] != self.context_generation or not self.context_busy:
            return
        self.context_busy = False
        block = result['text']
        if len(self.editor.string()) + len(block) > self.max_chars:
            self.refresh()
            self.status('There is not enough room to attach context.', error=True)
            return
        self.context_text = block
        insertion = self.editor.textStorage().length()
        self.editor.insertText_replacementRange_(block, (insertion, 0))
        self.editor.setSelectedRange_((insertion, 0))
        self.refresh()
        if result['page_missing']:
            self.notice_text = 'App attached. Its page was unavailable; check Automation permission for Ciel.'
            self.layout()

    def recent_(self, sender: Any) -> None:
        self.keyboard_footer = True
        if self.history_window is None:
            width = self.width
            self.history_window = HistoryPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                A.NSMakeRect(0, 0, width, 360), A.NSWindowStyleMaskBorderless, A.NSBackingStoreBuffered, False)
            self.history_window.owner = self
            self.history_window.setReleasedWhenClosed_(False)
            self.history_window.setLevel_(A.NSFloatingWindowLevel)
            self.history_window.setBackgroundColor_(color('0a1620'))
            self.history_window.setHasShadow_(True)
            view = self.history_window.contentView()
            view.setWantsLayer_(True)
            view.layer().setBackgroundColor_(color('0a1620').CGColor())
            view.layer().setBorderWidth_(1)
            view.layer().setBorderColor_(color('a0f0ff', 0.28).CGColor())
            title = A.NSTextField.labelWithString_('RECENT NOTES · THIS MAC')
            title.setFont_(A.NSFont.fontWithName_size_('Menlo', 11))
            title.setTextColor_(color('e8c98a'))
            title.setFrame_(A.NSMakeRect(20, 325, width - 100, 20))
            view.addSubview_(title)
            back = A.NSButton.buttonWithTitle_target_action_('BACK', self, 'closeHistory:')
            back.setBordered_(False)
            back.setContentTintColor_(color('8fa8b3'))
            back.setFrame_(A.NSMakeRect(width - 78, 319, 63, 30))
            view.addSubview_(back)
            self.history_search = A.NSTextField.alloc().initWithFrame_(A.NSMakeRect(20, 280, width - 40, 28))
            self.history_search.setPlaceholderString_('Find a note…')
            self.history_search.cell().setPlaceholderAttributedString_(A.NSAttributedString.alloc().initWithString_attributes_('Find a note…', {A.NSForegroundColorAttributeName: color('8fa8b3')}))
            self.history_search.setBezeled_(False)
            self.history_search.setDrawsBackground_(True)
            self.history_search.setTextColor_(color('dfe6e6'))
            self.history_search.setBackgroundColor_(color('0a1620'))
            self.history_search.setWantsLayer_(True)
            self.history_search.layer().setBorderWidth_(1)
            self.history_search.layer().setBorderColor_(color('a0f0ff', 0.28).CGColor())
            self.history_search.setDelegate_(self)
            view.addSubview_(self.history_search)
            scroll = A.NSScrollView.alloc().initWithFrame_(A.NSMakeRect(20, 18, width - 40, 250))
            scroll.setHasVerticalScroller_(True)
            scroll.setAutohidesScrollers_(True)
            scroll.setScrollerStyle_(A.NSScrollerStyleOverlay)
            scroll.setDrawsBackground_(False)
            self.history_text = HistoryText.alloc().initWithFrame_(A.NSMakeRect(0, 0, width - 40, 250))
            self.history_text.setEditable_(False)
            self.history_text.setRichText_(False)
            self.history_text.setDrawsBackground_(False)
            self.history_text.setTextColor_(color('dfe6e6'))
            self.history_text.setFont_(A.NSFont.systemFontOfSize_(14))
            self.history_text.setVerticallyResizable_(True)
            self.history_text.setAutoresizingMask_(A.NSViewWidthSizable)
            self.history_text.textContainer().setWidthTracksTextView_(True)
            scroll.setDocumentView_(self.history_text)
            view.addSubview_(scroll)
        self.history_error = ''
        self.render_history()
        frame = self.window.frame()
        screen = (self.window.screen() or A.NSScreen.mainScreen()).visibleFrame()
        self.history_window.setFrameOrigin_((frame.origin.x, max(screen.origin.y + 8, frame.origin.y + frame.size.height - 360)))
        self.history_window.makeKeyAndOrderFront_(None)
        self.history_window.makeFirstResponder_(self.history_search)
        print(json.dumps({'type': 'note.history'}), flush=True)

    def closeHistory_(self, sender: Any) -> None:
        if self.history_window is not None:
            self.history_window.orderOut_(None)
        self.window.makeKeyAndOrderFront_(None)
        self.window.makeFirstResponder_(self.editor)

    def controlTextDidChange_(self, notification: Any) -> None:
        self.render_history()

    @objc.python_method
    def render_history(self) -> None:
        query = str(self.history_search.stringValue()).casefold()
        rows = [row for row in self.history_items if query in row['text'].casefold()]
        blocks = []
        for row in rows:
            stamp = datetime.fromtimestamp(row['saved_at']).strftime('%b %d · %H:%M')
            blocks.append(f'{stamp}\n{row["text"]}')
        self.history_text.setString_('\n\n──────────\n\n'.join(blocks) or self.history_error or ('No matching notes.' if query else 'No confirmed notes on this Mac yet.\nNotes saved from now on will appear here.'))

    def save_(self, sender: Any) -> None:
        text = self.editor.string()
        if self.saving or self.draft_error or self.context_busy or self.dictation_session:
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
        self.saved = False
        self.editor.setEditable_(False)
        self.save_button.setEnabled_(False)
        self.status("Saving to memory…")
        print(json.dumps({"type": "note.save", "note_id": self.draft.note_id, "text": text}), flush=True)

    def hide_(self, sender: Any) -> None:
        abandoned = str(self.editor.string()) if not self.saving and not self.saved else ''
        abandoned_context = self.context_text
        try:
            self.draft.discard()
        except OSError:
            self.status("The draft could not be discarded. Try closing again.", error=True)
            return
        self.cancel_dictation()
        self.context_generation += 1
        self.context_busy = False
        self.context_text = ''
        if self.history_window is not None:
            self.history_window.orderOut_(None)
        self.window.orderOut_(None)
        self.cancel_saved_animation()
        origin = self.window.frame().origin
        self.editor.setString_("")
        self.editor.setEditable_(True)
        self.saving = self.saved = self.draft_error = False
        self.hover = self.keyboard_footer = False
        self.error_text = ""
        self.notice_text = ''
        self.refresh()
        self.window.setFrameOrigin_(origin)
        if self.previous_app is not None:
            self.previous_app.activateWithOptions_(A.NSApplicationActivateIgnoringOtherApps)
            self.previous_app = None
        if abandoned and self.undo_seconds:
            self.offer_undo(abandoned, abandoned_context)

    @objc.python_method
    def cancel_saved_animation(self) -> None:
        if self.saved_timer is not None:
            self.saved_timer.invalidate()
        self.saved_timer = None
        self.window.setAlphaValue_(1.0)
        if self.saved_origin is not None:
            self.window.setFrameOrigin_(self.saved_origin)
            self.saved_origin = None

    def savedHide_(self, timer: Any) -> None:
        if timer != self.saved_timer or self.saved_origin is None:
            return
        if self.editor.string() or self.saving or not self.saved:
            self.cancel_saved_animation()
            return
        progress = min(1.0, max(0.0, (time.monotonic() - self.saved_started) / 0.2))
        eased = progress * progress * (3 - 2 * progress)
        self.window.setAlphaValue_(1.0 - eased)
        if not self.saved_reduce_motion:
            x, y = self.saved_origin
            self.window.setFrameOrigin_((x, y - 6 * eased))
        if progress >= 1.0:
            self.hide_(None)

    def handleLine_(self, line: str) -> None:
        frame = json.loads(line)
        if frame["type"] == "quit":
            A.NSApplication.sharedApplication().terminate_(None)
        elif frame["type"] == "show":
            self.cancel_saved_animation()
            if not self.window.isVisible():
                self.previous_app = A.NSWorkspace.sharedWorkspace().frontmostApplication()
                self.source_app = (str(self.previous_app.localizedName() or ''), str(self.previous_app.bundleIdentifier() or '')) if self.previous_app is not None else ('', '')
            if self.saved:
                self.saved = False
                self.refresh()
            A.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            self.window.makeKeyAndOrderFront_(None)
            self.window.makeFirstResponder_(self.editor)
        elif frame['type'] == 'note.history.result':
            self.history_items = frame.get('items', [])
            self.history_error = frame.get('error', '')
            if self.history_window is not None:
                self.render_history()
        elif frame['type'] in ('note.dictation.state', 'note.dictation.result') and frame.get('session') == self.dictation_session and self.dictation_session:
            if frame['type'] == 'note.dictation.state':
                self.dictation_state = frame['state']
                self.refresh()
            else:
                self.dictation_session = self.dictation_state = ''
                self.editor.setEditable_(True)
                self.window.makeFirstResponder_(self.editor)
                self.refresh()
                text = frame.get('text', '')
                if frame.get('error'):
                    self.status(frame['error'], error=True)
                elif text:
                    # Insert through the native editor so draft persistence and selection agree.
                    selection = self.editor.selectedRange()
                    before = str(self.editor.string()).encode('utf-16-le')[:selection.location * 2].decode('utf-16-le')
                    if not selection.length and before and not before[-1].isspace():
                        text = ' ' + text
                    self.editor.insertText_replacementRange_(text, (A.NSNotFound, 0))
                else:
                    self.status('No speech heard. Your draft is unchanged.', error=True)
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
                self.saved = True
                self.refresh()
                self.status("Saved to memory.")
                if frame.get('history_error'):
                    self.state_label.setStringValue_('HISTORY ERROR')
                    self.state_label.setToolTip_(frame['history_error'])
                self.cancel_saved_animation()
                origin = self.window.frame().origin
                self.saved_origin = (origin.x, origin.y)
                self.saved_started = time.monotonic()
                self.saved_reduce_motion = A.NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion()
                self.saved_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(1 / 60, self, "savedHide:", None, True)
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
