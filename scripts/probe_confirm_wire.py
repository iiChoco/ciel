"""Probe the confirmation broker's spoke origin — a question over the wire.

    uv run scripts/probe_confirm_wire.py

The hub's voice lane asks its confirm-tier questions through the spoke:
the broker's remote choreography with origin ``spoke``, whose sink
speaks the line through the spoke and, for the lines that expect an
answer, opens its microphone (``listen``). This drives the real broker
with a fake sink and pins the contract the wire cut depends on: the
spoken lane's transcript labels (``you-confirm`` — the answer was
spoken *here*), ``listen`` on exactly the question and the re-prompt,
the spoke's own timeout arriving as an empty answer and reading as
"no answer", a spoke that dies mid-question resolving the hook to deny
within a second (a wedged hook is a dead assistant), and a late answer
after the verdict being ignored rather than approving the next thing.
"""

import asyncio
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ciel.config import Config, HubConfig
from ciel.confirm import VoiceConfirmBroker

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


class FakeSink:
    """The wire sink's shape: (text, listen) per line; can be dead."""

    def __init__(self, dead: bool = False):
        self.lines: list[tuple[str, bool]] = []
        self.dead = dead

    async def __call__(self, text: str, *, listen: bool = False) -> None:
        if self.dead:
            raise RuntimeError("no spoke to ask")
        self.lines.append((text, listen))


def make_broker(timeout_s: float = 5.0):
    cfg = replace(Config(), hub=replace(HubConfig(), confirm_timeout_s=timeout_s))
    broker = VoiceConfirmBroker(cfg)
    rows: list[tuple[str, str]] = []
    broker.bind_record(lambda speaker, text: rows.append((speaker, text)))
    return broker, rows


async def ask_via(broker, sink, question="Run: rm -rf build — okay?", origin="spoke"):
    with broker.remote(sink, origin=origin):
        return await broker.ask(question)


async def answer_soon(broker, text, delay=0.05):
    await asyncio.sleep(delay)
    return broker.answer(text)


async def main() -> None:
    print("the spoken answer, over the wire")
    broker, rows = make_broker()
    sink = FakeSink()
    verdict, accepted = await asyncio.gather(
        ask_via(broker, sink), answer_soon(broker, "yes go ahead")
    )
    check("a spoken yes approves", verdict is True and accepted)
    check(
        "the question went to the spoke with listen on",
        sink.lines == [("Run: rm -rf build — okay?", True)],
    )
    check(
        "the rows carry the spoken lane's labels",
        rows == [("ciel-confirm", "Run: rm -rf build — okay?"), ("you-confirm", "yes go ahead")],
    )
    check("the broker is idle again", not broker.active and broker.asked_at is None)

    broker, rows = make_broker()
    sink = FakeSink()
    verdict, _ = await asyncio.gather(ask_via(broker, sink), answer_soon(broker, "no"))
    check(
        "a spoken no denies, and the closing line is spoken without listening",
        verdict is False and sink.lines[-1] == ("Okay, skipping it.", False),
    )

    print("\nasking turned off")
    from ciel.config import ConfirmConfig
    broker, rows = make_broker()
    broker._config = replace(broker._config, confirm=ConfirmConfig(ask_first=False))
    sink = FakeSink()
    with broker.remote(sink, origin="spoke"):
        verdict = await broker.ask("Run: rm -rf build — okay?")
    check("with asking off, a confirm-tier question is a yes at once, and nothing goes to the room", verdict is True and sink.lines == [])
    check("the record still shows the question and who answered it",
          rows == [("ciel-confirm", "Run: rm -rf build — okay?"), ("you-confirm", "yes — confirmations are off")])
    shown: list[str] = []
    async def chart(text: str) -> None:
        shown.append(text)
    check("a grant's approval through the Chart is a yes the same way", await broker.ask_through(chart, "web", "Approve — okay?") is True and shown == [])
    with broker.suppress():
        check("an unattended turn still cannot act: nobody would have answered", await broker.ask("Send it — okay?") is False)
    check("the broker is idle throughout", not broker.active and broker.asked_at is None)

    print("\na question with no turn behind it, through one channel")
    broker, rows = make_broker(timeout_s=5.0)
    broker._config = replace(broker._config, discord=replace(broker._config.discord, confirm_timeout_s=1.0))
    shown: list[str] = []

    async def chart_send(text: str) -> None:
        shown.append(text)

    verdict, _ = await asyncio.gather(
        broker.ask_through(chart_send, "web", "Approve a standing grant — okay?"), answer_soon(broker, "yes")
    )
    check(
        "a yes over the channel approves, the question went through it, and the channel is gone again",
        verdict is True and shown == ["Approve a standing grant — okay?"] and broker._remote_send is None and not broker.active,
    )
    check("the rows label the answer as remote", rows[-1] == ("you-confirm-remote", "yes"))
    turn_sink = FakeSink()
    with broker.remote(turn_sink, origin="spoke"):
        verdict = await broker.ask_through(chart_send, "web", "Still there?")
        check(
            "while a turn has its channel installed, the question waits and then gives up rather than clobbering it",
            verdict is False and len(shown) == 1 and broker._remote_send is turn_sink,
        )
    verdict = await broker.ask_through(chart_send, "web", "Approve — okay?")
    check("no answer within the deadline is a no, and leaves the channel clear", verdict is False and broker._remote_send is None)

    broker, rows = make_broker()
    sink = FakeSink()

    async def unclear_then_yes():
        await asyncio.sleep(0.05)
        broker.answer("hmm what")
        await asyncio.sleep(0.1)
        broker.answer("yes")

    verdict, _ = await asyncio.gather(ask_via(broker, sink), unclear_then_yes())
    check(
        "an unclear answer earns one re-prompt, with listen on, then yes approves",
        verdict is True
        and [l for l in sink.lines if l[1]] == [("Run: rm -rf build — okay?", True), ("Yes or no?", True)],
    )

    print("\nthe spoke's own timeout")
    broker, rows = make_broker()
    sink = FakeSink()
    verdict, _ = await asyncio.gather(ask_via(broker, sink), answer_soon(broker, ""))
    check(
        "an empty answer (the spoke heard nothing) is no answer — deny",
        verdict is False and sink.lines[-1] == ("No answer — skipping it.", False),
    )
    check("and no you-confirm row is written for silence",
          all(r[0] != "you-confirm" for r in rows))

    broker, rows = make_broker(timeout_s=0.2)
    sink = FakeSink()
    started = time.monotonic()
    verdict = await ask_via(broker, sink)
    check(
        "a spoke that never reports at all hits the hub's deadline — deny",
        verdict is False and 0.15 < time.monotonic() - started < 1.0,
    )

    print("\nthe spoke dies mid-question")
    broker, rows = make_broker(timeout_s=30.0)
    sink = FakeSink()

    async def spoke_gone():
        await asyncio.sleep(0.1)
        broker.cancel("spoke gone")

    started = time.monotonic()
    verdict, _ = await asyncio.gather(ask_via(broker, sink), spoke_gone())
    check(
        "cancel resolves the hook to deny within a second",
        verdict is False and time.monotonic() - started < 1.0,
    )
    late = broker.answer("yes")
    check("a late yes after the verdict is ignored", late is False)

    broker, rows = make_broker()
    verdict = await ask_via(broker, FakeSink(dead=True))
    check("a question that cannot even be sent resolves to deny", verdict is False)

    print("\nthe text lanes keep their labels")
    broker, rows = make_broker()
    sink = FakeSink()

    async def plain_send(text):
        sink.lines.append((text, False))

    async def yes_soon():
        await asyncio.sleep(0.05)
        broker.answer("yes")

    verdict, _ = await asyncio.gather(
        ask_via(broker, plain_send, origin="discord"), yes_soon()
    )
    check(
        "a Discord-origin answer is still you-confirm-remote",
        verdict is True and rows[-1] == ("you-confirm-remote", "yes"),
    )

    print(f"\nall {len(CHECKS)} checks passed")


if __name__ == "__main__":
    asyncio.run(main())
