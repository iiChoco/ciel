"""The native scratchpad keeps its promises at the keyboard.

On macOS, render the real AppKit window over a temporary draft: initial focus,
Enter for a paragraph, Shift–Enter for one save, disabled editing in flight,
a visible error with its draft intact, successful receipt and clearing,
Escape and Discard remove unsaved drafts; an unexpected restart recovers them.
Tab focus, a narrow layout, and the real child process
startup, reuse, and shutdown. The supplied compact prototype also pins one-line
height, bounded growth and scrolling, hover and keyboard disclosure, pinned
error controls, compact saving and receipt rows, and position preservation.
The successful receipt tucks away within 400 ms while reopening cancels it.
Its brief fade drifts six points without moving the remembered position;
reopening restores full opacity, stale callbacks cannot hide a new note, and
Reduce Motion keeps the fade stationary.
Native background, text, accent, and receipt colours retain the prototype's
sRGB values through the AppKit and Core Animation drawing paths.
Command shortcuts are exercised through the window, including full Unicode
selection, replacing a selection, clipboard action dispatch, modifier and focus
boundaries, and disabled editing during a save. Clipboard actions are captured
at their native entry points so the owner's clipboard is never read or changed.
Recent preserves and searches alongside the draft; Undo is memory-only and
expires; context is visible, removable, and fenced against late completion;
dictation waits for microphone acknowledgement, inserts ahead of context,
and cannot resurrect a discarded draft. Browser and speech results are fixtures.
The window talks to a
fixture receipt through captured stdout; it never reads config or real memory.
Optional --screenshots DIRECTORY writes native view images for visual review.

    uv run --no-sync python scripts/probe_note_window.py
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


def main() -> None:
    if sys.platform != 'darwin':
        print('native note window requires macOS; not run')
        return
    import AppKit as A
    from ciel.ui.notes import NoteController
    from ciel.notes import Draft, NoteWindow
    from ciel.config import NotesConfig
    app = A.NSApplication.sharedApplication()
    app.setActivationPolicy_(A.NSApplicationActivationPolicyAccessory)
    destination = Path(sys.argv[2]) if len(sys.argv) == 3 and sys.argv[1] == '--screenshots' else None

    def matches_srgb(value: object, expected: str, alpha: float = 1.0) -> bool:
        converted = value.colorUsingColorSpace_(A.NSColorSpace.sRGBColorSpace())
        channels = (converted.redComponent(), converted.greenComponent(), converted.blueComponent())
        return tuple(round(c * 255) for c in channels) == tuple(bytes.fromhex(expected)) and abs(converted.alphaComponent() - alpha) < 0.001

    def shot(controller: NoteController, name: str, view: object | None = None) -> None:
        if destination is None:
            return
        destination.mkdir(parents=True, exist_ok=True)
        view = view or controller.container
        view.displayIfNeeded()
        bitmap = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
        view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), bitmap)
        data = bitmap.representationUsingType_properties_(A.NSBitmapImageFileTypePNG, {})
        data.writeToFile_atomically_(str(destination / f'{name}.png'), True)

    def key(controller: NoteController, code: int, chars: str, flags: int = 0, repeat: bool = False) -> None:
        event = A.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
            A.NSEventTypeKeyDown, (0, 0), flags, 0, controller.window.windowNumber(), None, chars, chars, repeat, code
        )
        controller.editor.keyDown_(event)

    def command(controller: NoteController, chars: str, extra: int = 0) -> bool:
        event = A.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
            A.NSEventTypeKeyDown, (0, 0), A.NSEventModifierFlagCommand | extra, 0,
            controller.window.windowNumber(), None, chars, chars, False, 0
        )
        return controller.window.performKeyEquivalent_(event)

    with tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)):
        config = {'dir': temp, 'max_chars': 16000}
        controller = NoteController.alloc().initWithConfig_(config)
        controller.handleLine_('{"type":"show"}')
        check('the window opens with the editor as its first responder', controller.window.firstResponder() == controller.editor)
        check('an empty note cannot be saved', not controller.save_button.isEnabled())
        check('an empty thought opens as a compact bar', controller.window.frame().size.width == 560 and controller.window.frame().size.height == 59)
        check('the resting bar offers only the inline save hint', controller.footer.isHidden() and not controller.top_hint.isHidden())
        check('the native background keeps the prototype sRGB shade', matches_srgb(A.NSColor.colorWithCGColor_(controller.container.layer().backgroundColor()), '0a1620'))
        check('the editor and gold hint keep their prototype colours and opacity', matches_srgb(controller.editor.textColor(), 'dfe6e6') and matches_srgb(controller.top_hint.textColor(), 'e8c98a', 0.55))
        shot(controller, 'empty')
        controller.editor.insertText_replacementRange_('A small idea for the moon garden.', (0, 0))
        key(controller, 36, '\r')
        controller.editor.insertText_replacementRange_('White flowers, a bench, and a telescope.', (A.NSNotFound, 0))
        check('Enter adds a paragraph instead of submitting', '\n' in controller.editor.string() and not controller.saving)
        check('typing is kept in a private draft', Draft.load(Path(temp) / 'draft.json').text == controller.editor.string())
        check('paragraphs grow the bar while the footer stays tucked away', controller.window.frame().size.height > 59 and controller.footer.isHidden())
        check('typing removes the empty-bar save hint', controller.top_hint.isHidden())
        check('Command A selects the whole note through the window shortcut path', command(controller, 'a') and controller.editor.selectedRange() == (0, controller.editor.textStorage().length()))
        original = str(controller.editor.string())
        controller.editor.insertText_replacementRange_('An idea 🌙\nAnother line.', (A.NSNotFound, 0))
        check('typing after Select All replaces the note and persists the replacement', controller.editor.string() == 'An idea 🌙\nAnother line.' and Draft.load(Path(temp) / 'draft.json').text == controller.editor.string())
        check('Select All includes Unicode characters and ignores Caps Lock', command(controller, 'A', A.NSEventModifierFlagCapsLock) and controller.editor.selectedRange() == (0, controller.editor.textStorage().length()))
        controller.editor.insertText_replacementRange_(original, (A.NSNotFound, 0))
        selection = controller.editor.selectedRange()
        command(controller, 'a', A.NSEventModifierFlagOption)
        check('Command Option A does not become Select All', controller.editor.selectedRange() == selection)
        actions = []
        for letter, selector in (('x', 'cut_'), ('c', 'copy_'), ('v', 'paste_')):
            def capture(sender: object) -> None:
                actions.append(letter)
            with patch.object(controller.editor, selector, capture):
                command(controller, letter)
        check('Cut Copy and Paste reach their native editor actions', actions == ['x', 'c', 'v'])
        shot(controller, 'draft')
        top = controller.window.frame().origin.y + controller.window.frame().size.height
        controller.container.mouseEntered_(None)
        check('hover reveals the count and native controls', not controller.footer.isHidden() and not controller.save_button.isHiddenOrHasHiddenAncestor())
        check('revealing controls preserves the top edge', controller.window.frame().origin.y + controller.window.frame().size.height == top)
        shot(controller, 'hover')
        controller.container.mouseExited_(None)
        check('leaving the bar tucks pointer-only controls away', controller.footer.isHidden())
        key(controller, 48, '\t')
        check('Tab moves from the editor to Save', controller.window.firstResponder() == controller.save_button)
        controller.container.mouseExited_(None)
        check('keyboard focus keeps the footer visible after the pointer leaves', not controller.footer.isHidden())
        selection = controller.editor.selectedRange()
        command(controller, 'a')
        check('Select All does not steal focus from the Save button', controller.window.firstResponder() == controller.save_button and controller.editor.selectedRange() == selection)
        shot(controller, 'keyboard')
        controller.window.makeFirstResponder_(controller.editor)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            key(controller, 36, '\r', A.NSEventModifierFlagShift)
            key(controller, 36, '\r', A.NSEventModifierFlagShift, repeat=True)
        frames = [json.loads(line) for line in output.getvalue().splitlines()]
        check('Shift Enter submits once even when held', len(frames) == 1 and frames[0]['type'] == 'note.save')
        check('an outstanding save cannot be edited or resubmitted', controller.saving and not controller.editor.isEditable() and not controller.save_button.isEnabled())
        actions = []
        with patch.object(controller.editor, 'paste_', capture):
            command(controller, 'v')
        check('Paste cannot change a note while its receipt is outstanding', not actions and controller.editor.string() == frames[0]['text'])
        check('saving folds to one line with a visible progress label', controller.window.frame().size.height == 59 and controller.scroll.isHidden() and controller.state_label.stringValue() == 'SAVING TO MEMORY…')
        shot(controller, 'saving')
        controller.handleLine_(json.dumps({'type': 'note.result', 'note_id': frames[0]['note_id'], 'ok': False, 'error': 'The brain is offline. Your draft is here; retry when connected.'}))
        check('a failed receipt keeps the text and permits retry', not controller.saving and controller.editor.isEditable() and controller.editor.string() == frames[0]['text'])
        check('the save error remains visible', 'offline' in controller.message.stringValue())
        controller.container.mouseExited_(None)
        check('a failed save pins the error and Retry without hover', not controller.footer.isHidden() and not controller.message.isHidden() and controller.save_button.title() == 'RETRY  ⇧↵')
        shot(controller, 'error')
        check('a failed save keeps the prototype red', matches_srgb(A.NSColor.colorWithCGColor_(controller.dot.backgroundColor()), 'e88a8a'))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            key(controller, 36, '\r', A.NSEventModifierFlagShift)
        retry = json.loads(output.getvalue())
        check('the native retry keeps the same note identity', retry['note_id'] == frames[0]['note_id'])
        controller.handleLine_(json.dumps({'type': 'note.result', 'note_id': retry['note_id'], 'ok': True}))
        check('a successful receipt clears the editor and draft', not controller.editor.string() and not (Path(temp) / 'draft.json').exists())
        check('success is visible before the window tucks away', controller.summary.stringValue() == 'Saved to memory.' and not controller.summary.isHidden())
        check('a successful receipt is a compact green row with a tuck-away hint', controller.window.frame().size.height == 59 and controller.state_label.stringValue() == 'TUCKING AWAY' and controller.scroll.isHidden())
        shot(controller, 'saved')
        check('a memory receipt keeps the prototype green', matches_srgb(controller.summary.textColor(), 'a6ecc7') and matches_srgb(A.NSColor.colorWithCGColor_(controller.dot.backgroundColor()), '7adea8'))
        controller.handleLine_('{"type":"show"}')
        check('reopening during the receipt cancels tucking away and restores editing', controller.saved_timer is None and not controller.scroll.isHidden() and not controller.saved)
        controller.editor.insertText_replacementRange_('An unfinished thought.', (0, 0))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            key(controller, 53, '\x1b')
        check('Escape discards an unfinished thought from the editor and disk', not controller.window.isVisible() and not controller.editor.string() and not controller.draft.note_id and not (Path(temp) / 'draft.json').exists())
        check('closing an unsaved note sends no memory request', not output.getvalue())
        controller.handleLine_('{"type":"show"}')
        check('reopening a discarded note starts blank with Save disabled', not controller.editor.string() and not controller.save_button.isEnabled())
        controller.editor.insertText_replacementRange_('An idea to discard.', (0, 0))
        with patch.object(Draft, 'discard', side_effect=PermissionError('fixture')):
            controller.close_button.performClick_(None)
        check('a failed discard stays visible and reports that the draft remains', controller.window.isVisible() and controller.editor.string() == 'An idea to discard.' and (Path(temp) / 'draft.json').exists() and 'could not be discarded' in controller.message.stringValue())
        controller.close_button.performClick_(None)
        check('the Discard button clears the draft just like Escape', controller.close_button.title() == 'ESC DISCARD' and not controller.window.isVisible() and not controller.editor.string() and not (Path(temp) / 'draft.json').exists())
        controller.draft.update('An unfinished thought.')
        reopened = NoteController.alloc().initWithConfig_(config)
        check('a new native window restores the draft after restart', reopened.editor.string() == 'An unfinished thought.')
        reopened.window.orderOut_(None)

        timed = NoteController.alloc().initWithConfig_({'dir': str(Path(temp) / 'timed')})
        timed.handleLine_('{"type":"show"}')
        timed.previous_app = None
        timed.editor.insertText_replacementRange_('A thought, then back to work.', (0, 0))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            key(timed, 36, '\r', A.NSEventModifierFlagShift)
        request = json.loads(output.getvalue())
        timed.handleLine_(json.dumps({'type': 'note.result', 'note_id': request['note_id'], 'ok': True}))
        origin = timed.saved_origin
        timed.saved_reduce_motion = False
        with patch('ciel.ui.notes.time.monotonic', return_value=timed.saved_started + 0.1):
            timed.savedHide_(timed.saved_timer)
        check('the receipt fades and drifts only a few points midway through dismissal', 0.4 < timed.window.alphaValue() < 0.6 and abs(timed.window.frame().origin.y - (origin[1] - 3)) < 1)
        A.NSRunLoop.currentRunLoop().runUntilDate_(A.NSDate.dateWithTimeIntervalSinceNow_(0.4))
        check('a successful note tucks away within a brief glance', not timed.window.isVisible() and timed.saved_timer is None)
        check('dismissal restores opacity and the remembered position for next time', timed.window.alphaValue() == 1 and timed.window.frame().origin == origin)
        timed.handleLine_('{"type":"show"}')
        timed.editor.insertText_replacementRange_('Another passing thought.', (0, 0))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            key(timed, 36, '\r', A.NSEventModifierFlagShift)
        request = json.loads(output.getvalue())
        with patch('ciel.ui.notes.A.NSWorkspace') as workspace:
            workspace.sharedWorkspace.return_value.accessibilityDisplayShouldReduceMotion.return_value = True
            timed.handleLine_(json.dumps({'type': 'note.result', 'note_id': request['note_id'], 'ok': True}))
        old_timer = timed.saved_timer
        origin = timed.saved_origin
        with patch('ciel.ui.notes.time.monotonic', return_value=timed.saved_started + 0.1):
            timed.savedHide_(old_timer)
        check('Reduce Motion fades the receipt without moving it', 0.4 < timed.window.alphaValue() < 0.6 and timed.window.frame().origin == origin)
        timed.handleLine_('{"type":"show"}')
        check('reopening during the fade restores a fully visible editor in place', timed.window.isVisible() and timed.window.alphaValue() == 1 and timed.window.frame().origin == origin and timed.saved_timer is None and not old_timer.isValid())
        timed.savedHide_(old_timer)
        check('an old animation callback cannot hide a reopened note', timed.window.isVisible() and timed.window.alphaValue() == 1)
        timed.hide_(None)

        timed.handleLine_('{"type":"show"}')
        timed.editor.insertText_replacementRange_('Already submitted.', (0, 0))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            key(timed, 36, '\r', A.NSEventModifierFlagShift)
        pending = json.loads(output.getvalue())
        key(timed, 53, '\x1b')
        timed.handleLine_('{"type":"show"}')
        timed.editor.insertText_replacementRange_('A fresh unsaved thought.', (0, 0))
        timed.handleLine_(json.dumps({'type': 'note.result', 'note_id': pending['note_id'], 'ok': True}))
        check('a late receipt for a closed note cannot clear the next draft', timed.editor.string() == 'A fresh unsaved thought.' and timed.window.isVisible() and not timed.saving and not timed.saved and timed.draft.text == timed.editor.string())
        timed.hide_(None)

        class Screen:
            def visibleFrame(self) -> object:
                return A.NSMakeRect(0, 0, 440, 700)
        with patch('ciel.ui.notes.A.NSScreen') as screens:
            screens.mainScreen.return_value = Screen()
            narrow = NoteController.alloc().initWithConfig_(config)
        narrow.handleLine_('{"type":"show"}')
        check('the note fits a narrow screen', narrow.window.frame().size.width == 400 and narrow.scroll.frame().size.width == 344)
        narrow.container.mouseEntered_(None)
        check('the count moves above the controls at narrow width', narrow.count.frame().origin.y > narrow.save_button.frame().origin.y + narrow.save_button.frame().size.height)
        check('the footer controls do not overlap at narrow width', narrow.close_button.frame().origin.x + narrow.close_button.frame().size.width < narrow.save_button.frame().origin.x)
        shot(narrow, 'narrow')
        narrow.status('The brain is offline. Your draft is here; retry when connected.', error=True)
        check('a narrow error wraps without hiding its retry action', narrow.message.frame().size.height > narrow.message.attributedStringValue().size().height and not narrow.footer.isHidden())
        shot(narrow, 'narrow-error')
        narrow.editor.setString_('A thought that needs more room.\n' * 30)
        narrow.textDidChange_(None)
        check('long notes stop growing at the prototype height and can scroll', narrow.scroll.frame().size.height == 180 and narrow.editor.frame().size.height > 180)
        shot(narrow, 'long')
        narrow.editor.setString_('x' * 16001)
        narrow.textDidChange_(None)
        check('oversized notes remain editable with an error instead of being truncated', len(narrow.editor.string()) == 16001 and not narrow.save_button.isEnabled() and not narrow.message.isHidden())
        narrow.editor.setString_('A shorter thought.')
        narrow.textDidChange_(None)
        check('editing a failed note clears its error and restores Save', not narrow.error_text and narrow.save_button.isEnabled() and narrow.save_button.title() == 'SAVE  ⇧↵')
        narrow.window.setFrameOrigin_((120, 400))
        origin = narrow.window.frame().origin
        narrow.hide_(None)
        narrow.handleLine_('{"type":"show"}')
        check('reopening keeps the position the owner chose', narrow.window.frame().origin == origin)
        narrow.window.orderOut_(None)
        controller.window.orderOut_(None)

        extras = NoteController.alloc().initWithConfig_({'dir': str(Path(temp) / 'extras'), 'undo_discard_s': 5})
        extras.handleLine_('{"type":"show"}')
        extras.previous_app = None
        extras.editor.insertText_replacementRange_('Undo this passing thought.', (0, 0))
        extras.hide_(None)
        check('discard offers Undo in memory while removing the draft from disk', extras.undo_text == 'Undo this passing thought.' and extras.undo_panel.isVisible() and not extras.draft.path.exists())
        shot(extras, 'undo', extras.undo_panel.contentView())
        extras.undoDiscard_(None)
        check('Undo restores the draft for editing without a memory save', extras.editor.string() == 'Undo this passing thought.' and extras.draft.path.exists() and not extras.saving and not extras.undo_text)
        extras.previous_app = None
        extras.hide_(None)
        with patch('ciel.ui.notes.time.monotonic', return_value=extras.undo_deadline + 0.01):
            extras.undoDiscard_(None)
        check('Undo expires after five seconds without recreating the draft', not extras.editor.string() and not extras.draft.path.exists() and not extras.undo_text)
        extras.handleLine_('{"type":"show"}')
        extras.editor.insertText_replacementRange_('Keep this draft while browsing.', (0, 0))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            extras.recent_(None)
        check('Recent requests confirmed history without submitting the draft', json.loads(output.getvalue()) == {'type': 'note.history'} and not extras.saving)
        extras.handleLine_(json.dumps({'type': 'note.history.result', 'items': [
            {'note_id': '1' * 32, 'text': 'Build a moon garden.\nWhite flowers and a telescope.', 'saved_at': 1788973200},
            {'note_id': '2' * 32, 'text': 'Read the book about tides.', 'saved_at': 1788969600},
        ]}))
        check('Recent displays full saved text in a selectable read-only view', 'moon garden' in extras.history_text.string() and 'tides' in extras.history_text.string() and not extras.history_text.isEditable())
        shot(extras, 'recent', extras.history_window.contentView())
        extras.history_search.setStringValue_('MOON')
        extras.controlTextDidChange_(None)
        check('history search filters without changing the open draft', 'moon garden' in extras.history_text.string() and 'tides' not in extras.history_text.string() and extras.editor.string() == 'Keep this draft while browsing.')
        extras.closeHistory_(None)
        check('leaving Recent restores editor focus without discarding it', extras.window.firstResponder() == extras.editor and extras.draft.text == 'Keep this draft while browsing.')
        extras.source_app = ('Safari', 'com.apple.Safari')
        block = '\n\nFrom Safari\n"An idea"\nhttps://example.com/idea'
        with patch('ciel.ui.notes.capture_context', return_value=(block, False)) as capture:
            extras.context_(None)
            A.NSRunLoop.currentRunLoop().runUntilDate_(A.NSDate.dateWithTimeIntervalSinceNow_(0.1))
        check('Context reads only on request and appends a visible removable attachment', capture.call_args.args == ('Safari', 'com.apple.Safari') and extras.editor.string().endswith(block) and extras.context_button.title() == 'REMOVE CONTEXT' and extras.editor.selectedRange().location == len('Keep this draft while browsing.'))
        extras.container.mouseEntered_(None)
        shot(extras, 'context')
        extras.context_(None)
        check('removing context leaves the original wording intact', extras.editor.string() == 'Keep this draft while browsing.' and not extras.context_text)
        extras.context_busy = True
        generation = extras.context_generation
        extras.hide_(None)
        extras.contextReady_(json.dumps({'generation': generation, 'text': block, 'page_missing': False}))
        check('context arriving after discard cannot recreate the draft', not extras.editor.string() and not extras.draft.path.exists())
        extras.handleLine_('{"type":"show"}')
        extras.editor.insertText_replacementRange_('Start: ', (0, 0))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            extras.dictate_(None)
            extras.save_(None)
        start = json.loads(output.getvalue())
        check('Dictate requests listening and prevents saving unfinished speech', start['action'] == 'start' and not extras.editor.isEditable() and not extras.save_button.isEnabled() and not extras.saving)
        check('Dictate waits for the microphone acknowledgement before claiming to listen', extras.dictation_state == 'starting' and extras.dictate_button.title() == 'STARTING…')
        extras.handleLine_(json.dumps({'type': 'note.dictation.state', 'session': start['session'], 'state': 'listening'}))
        shot(extras, 'dictation')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            extras.dictate_(None)
        check('Stop Dictation requests transcription once', json.loads(output.getvalue())['action'] == 'stop' and extras.dictation_state == 'transcribing')
        extras.handleLine_(json.dumps({'type': 'note.dictation.result', 'session': start['session'], 'text': 'a spoken idea'}))
        check('dictated words enter the editor and draft without a memory save', extras.editor.string() == 'Start: a spoken idea' and extras.draft.text == extras.editor.string() and extras.editor.isEditable() and not extras.saving)
        extras.context_text = block
        extras.editor.insertText_replacementRange_(block, (extras.editor.textStorage().length(), 0))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            extras.dictate_(None)
        start = json.loads(output.getvalue())
        extras.handleLine_(json.dumps({'type': 'note.dictation.result', 'session': start['session'], 'text': 'with more detail'}))
        check('dictated words stay in the note body ahead of attached context', extras.editor.string() == 'Start: a spoken idea with more detail' + block)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            extras.dictate_(None)
        start = json.loads(output.getvalue())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            extras.hide_(None)
        check('discard cancels microphone capture before clearing the editor', json.loads(output.getvalue())['action'] == 'cancel' and not extras.dictation_session and not extras.editor.string())
        extras.handleLine_(json.dumps({'type': 'note.dictation.result', 'session': start['session'], 'text': 'late words'}))
        check('a late transcript cannot resurrect a discarded note', not extras.editor.string() and not extras.draft.path.exists())
        extras.forgetUndo_(None)

        narrow.handleLine_('{"type":"show"}')
        with contextlib.redirect_stdout(io.StringIO()):
            narrow.recent_(None)
        narrow.handleLine_(json.dumps({'type': 'note.history.result', 'items': extras.history_items}))
        check('Recent fits a narrow screen with room for search and saved text', narrow.history_window.frame().size.width == 400 and narrow.history_search.frame().size.width == 360)
        shot(narrow, 'recent-narrow', narrow.history_window.contentView())
        narrow.closeHistory_(None)
        narrow.context_busy = True
        narrow.context_generation += 1
        narrow.contextReady_(json.dumps({'generation': narrow.context_generation, 'text': '\n\nFrom Safari', 'page_missing': True}))
        check('an app-only context fallback explains itself without pretending Save failed', not narrow.error_text and not narrow.message.isHidden() and narrow.save_button.title() == 'SAVE  ⇧↵')
        shot(narrow, 'context-fallback')
        narrow.hide_(None)
        narrow.forgetUndo_(None)

        async def lifecycle() -> None:
            async def save(note_id: str, text: str) -> dict:
                raise AssertionError('the lifecycle probe does not submit notes')
            bridge = NoteWindow(NotesConfig(dir=Path(temp)), save)
            await bridge.show()
            await asyncio.sleep(0.5)
            process = bridge._proc
            check('the real native child starts and keeps its pipe open', process.returncode is None)
            await bridge.show()
            check('opening again reuses the same native child', bridge._proc is process)
            await bridge.close()
            check('closing the bridge reaps the native child', process.returncode == 0)
        asyncio.run(lifecycle())
    print(f'\nall {len(CHECKS)} checks passed')


if __name__ == '__main__':
    main()
