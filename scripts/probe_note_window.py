"""The native scratchpad keeps its promises at the keyboard.

On macOS, render the real AppKit window over a temporary draft: initial focus,
Enter for a paragraph, Shift–Enter for one save, disabled editing in flight,
a visible error with its draft intact, successful receipt and clearing,
Escape and restart, Tab focus, a narrow layout, and the real child process startup, reuse, and shutdown. The window talks to a
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

    def shot(controller: NoteController, name: str) -> None:
        if destination is None:
            return
        destination.mkdir(parents=True, exist_ok=True)
        view = controller.container
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

    with tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)):
        config = {'dir': temp, 'max_chars': 16000}
        controller = NoteController.alloc().initWithConfig_(config)
        controller.handleLine_('{"type":"show"}')
        check('the window opens with the editor as its first responder', controller.window.firstResponder() == controller.editor)
        check('an empty note cannot be saved', not controller.save_button.isEnabled())
        shot(controller, 'empty')
        controller.editor.insertText_replacementRange_('A small idea for the moon garden.', (0, 0))
        key(controller, 36, '\r')
        controller.editor.insertText_replacementRange_('White flowers, a bench, and a telescope.', (A.NSNotFound, 0))
        check('Enter adds a paragraph instead of submitting', '\n' in controller.editor.string() and not controller.saving)
        check('typing is kept in a private draft', Draft.load(Path(temp) / 'draft.json').text == controller.editor.string())
        shot(controller, 'draft')
        key(controller, 48, '\t')
        check('Tab moves from the editor to Save', controller.window.firstResponder() == controller.save_button)
        controller.window.makeFirstResponder_(controller.editor)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            key(controller, 36, '\r', A.NSEventModifierFlagShift)
            key(controller, 36, '\r', A.NSEventModifierFlagShift, repeat=True)
        frames = [json.loads(line) for line in output.getvalue().splitlines()]
        check('Shift Enter submits once even when held', len(frames) == 1 and frames[0]['type'] == 'note.save')
        check('an outstanding save cannot be edited or resubmitted', controller.saving and not controller.editor.isEditable() and not controller.save_button.isEnabled())
        shot(controller, 'saving')
        controller.handleLine_(json.dumps({'type': 'note.result', 'note_id': frames[0]['note_id'], 'ok': False, 'error': 'The brain is offline. Your draft is here; retry when connected.'}))
        check('a failed receipt keeps the text and permits retry', not controller.saving and controller.editor.isEditable() and controller.editor.string() == frames[0]['text'])
        check('the save error remains visible', 'offline' in controller.message.stringValue())
        shot(controller, 'error')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            key(controller, 36, '\r', A.NSEventModifierFlagShift)
        retry = json.loads(output.getvalue())
        check('the native retry keeps the same note identity', retry['note_id'] == frames[0]['note_id'])
        controller.handleLine_(json.dumps({'type': 'note.result', 'note_id': retry['note_id'], 'ok': True}))
        check('only a successful receipt clears the editor and draft', not controller.editor.string() and not (Path(temp) / 'draft.json').exists())
        check('success is visible before the window tucks away', controller.message.stringValue() == 'Saved to memory.')
        shot(controller, 'saved')
        controller.handleLine_('{"type":"show"}')
        controller.editor.insertText_replacementRange_('An unfinished thought.', (0, 0))
        key(controller, 53, '\x1b')
        check('Escape hides without losing an unfinished thought', not controller.window.isVisible() and Draft.load(Path(temp) / 'draft.json').text == 'An unfinished thought.')
        reopened = NoteController.alloc().initWithConfig_(config)
        check('a new native window restores the draft after restart', reopened.editor.string() == 'An unfinished thought.')
        reopened.window.orderOut_(None)

        class Screen:
            def visibleFrame(self) -> object:
                return A.NSMakeRect(0, 0, 440, 700)
        with patch('ciel.ui.notes.A.NSScreen') as screens:
            screens.mainScreen.return_value = Screen()
            narrow = NoteController.alloc().initWithConfig_(config)
        narrow.handleLine_('{"type":"show"}')
        check('the note fits a narrow screen', narrow.window.frame().size.width == 400 and narrow.scroll.frame().size.width == 348)
        check('the footer controls do not overlap at narrow width', narrow.save_button.frame().origin.x >= 200)
        shot(narrow, 'narrow')
        narrow.window.orderOut_(None)
        controller.window.orderOut_(None)

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
