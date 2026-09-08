"""Reproduce the follow-up loop's state transitions without audio or runtime state.

Pins that spoken replies reopen follow-up capture repeatedly, speech in hand
outlives its deadline in both loops, and disabling follow-ups returns to waiting.
This supplies the speech verdict; it does not claim to reproduce acoustic VAD.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        raise SystemExit(1)


def method(path: str, cls: str, name: str) -> Any:
    tree = ast.parse((ROOT / path).read_text())
    owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    node = next(n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), node], type_ignores=[])
    scope: dict[str, Any] = {'time': SimpleNamespace(monotonic=lambda: 100.0)}
    exec(compile(ast.fix_missing_locations(module), path, 'exec'), scope)
    return scope[name]


def main() -> None:
    finish = method('src/ciel/spoke/frontend.py', 'Spoke', '_finish_turn')
    state = SimpleNamespace(value='busy')
    spoke = SimpleNamespace(
        _shortcut_quiet=False, _muted=False, _continue_listening=False, _spoke=True,
        _config=SimpleNamespace(audio=SimpleNamespace(followup_ms=5000)),
        _enter_followup=lambda: setattr(state, 'value', 'listening'),
        _enter_waiting=lambda: setattr(state, 'value', 'waiting'),
    )
    mic = SimpleNamespace(drain=lambda: None)
    for turn in range(3):
        state.value = 'busy'
        finish(spoke, mic)
        check(f'spoken reply {turn + 1} opens another listening window', state.value == 'listening')
    for path, cls in [('src/ciel/spoke/frontend.py', 'Spoke'), ('src/ciel/pipeline.py', 'Pipeline')]:
        expired = method(path, cls, '_followup_expired')
        window = SimpleNamespace(_followup_until=1.0, _endpointer=SimpleNamespace(speaking=True))
        check(f'{cls} retains speech after the follow-up deadline', not expired(window))
        window._endpointer.speaking = False
        check(f'{cls} expires an unused window', expired(window))
    spoke._config.audio.followup_ms = 0
    finish(spoke, mic)
    check('disabled follow-ups return the spoke to waiting', state.value == 'waiting')
    print(f'\nall {len(CHECKS)} checks passed')


if __name__ == '__main__':
    main()
