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
An answer names its question: the sink registers the id it puts each
listening line under, an answer carrying the id of a question already
decided is refused while the next question stays open, the re-prompt
takes a new id so a late answer to the first wording is refused too, an
answer with no id (the Chart's row, a typed line) is judged by the
predating rule alone, and the identity is cleared with the verdict and
on cancellation. The opt-in scoped path instead requires the exact question,
operation, digest, initiating client, and unexpired deadline; generic answers,
confirmations-off, cancellation, and concurrent requests cannot approve it.
"""

from __future__ import annotations

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
    broker._config = replace(broker._config, web=replace(broker._config.web, confirm_timeout_s=1.0))
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
        ask_via(broker, plain_send, origin="web"), yes_soon()
    )
    check(
        "a web-origin answer is still you-confirm-remote",
        verdict is True and rows[-1] == ("you-confirm-remote", "yes"),
    )

    print("\nan answer names its question")

    class IdSink(FakeSink):
        """The spoke sink's shape: every listening line goes out under an
        id the broker is told to expect, the way _WireSink mints one."""

        def __init__(self, broker):
            super().__init__()
            self.broker = broker
            self.ids: list[str] = []

        async def __call__(self, text: str, *, listen: bool = False) -> None:
            if listen:
                self.ids.append(f"q{len(self.ids) + 1}")
                self.broker.expect(self.ids[-1])
            await super().__call__(text, listen=listen)

    broker, rows = make_broker()
    sink = IdSink(broker)
    verdict, _ = await asyncio.gather(ask_via(broker, sink, "Fixture action A?"), answer_soon(broker, "no"))
    check("question A is denied and its identity is cleared with the verdict", verdict is False and broker._expected_id is None)

    async def a_stale_yes_then_the_real_answer():
        await asyncio.sleep(0.05)
        stale = broker.answer("yes", confirm_id="q1")
        await asyncio.sleep(0.05)
        still_open = broker.active
        fresh = broker.answer("no", confirm_id="q2")
        return stale, still_open, fresh

    verdict, (stale, still_open, fresh) = await asyncio.gather(
        ask_via(broker, sink, "Fixture action B?"), a_stale_yes_then_the_real_answer()
    )
    check("a yes carrying A's id arriving while B is open is refused, and B stays open for its own answer",
          stale is False and still_open and fresh is True and verdict is False and sink.ids == ["q1", "q2"])

    async def an_unclear_answer_then_a_late_yes_to_the_first_wording():
        await asyncio.sleep(0.05)
        broker.answer("hmm what", confirm_id="q3")
        await asyncio.sleep(0.1)
        late = broker.answer("yes", confirm_id="q3")
        await asyncio.sleep(0.05)
        return late, broker.answer("yes", confirm_id="q4")

    verdict, (late, reprompted) = await asyncio.gather(
        ask_via(broker, sink, "Fixture action C?"), an_unclear_answer_then_a_late_yes_to_the_first_wording()
    )
    check("the re-prompt takes a new id: a yes to the first wording after it is refused, a yes to the re-prompt approves",
          late is False and reprompted is True and verdict is True and sink.ids[-2:] == ["q3", "q4"])

    verdict, accepted = await asyncio.gather(ask_via(broker, sink, "Fixture action D?"), answer_soon(broker, "yes"))
    check("an answer with no id — the Chart's row, a typed line — is still judged by the predating rule alone",
          verdict is True and accepted)

    async def cancelled_mid_question():
        await asyncio.sleep(0.05)
        broker.cancel("spoke gone")

    verdict, _ = await asyncio.gather(ask_via(broker, sink, "Fixture action E?"), cancelled_mid_question())
    check("cancellation clears the identity too, so nothing can be answered under it later",
          verdict is False and broker._expected_id is None and broker.answer("yes", confirm_id=sink.ids[-1]) is False)

    print("\nstrict private questions")
    broker,rows=make_broker();shown=[]
    async def scoped_send(scope: dict) -> None:shown.append(scope)
    async def start_scoped(client: str="page-a", seconds: float=1.0):
        task=asyncio.create_task(broker.ask_scoped(scoped_send,operation="bulk_apply",digest="review-digest",client=client,expires=time.monotonic()+seconds))
        for _ in range(100):
            if broker._scope is not None:return task
            if task.done():return task
            await asyncio.sleep(.001)
        raise AssertionError("scope was not shown")
    def scoped_answer(**changes):
        values={"confirm_id":shown[-1]["confirm_id"],"operation":"bulk_apply","digest":"review-digest","client":"page-a","approve":True}
        return broker.answer_scoped(**{**values,**changes})
    task=await start_scoped()
    check("a strict question is private and not copied to the transcript",broker.active and not rows and len(shown)==1)
    check("a generic keyboard or Chat yes cannot answer a strict question",not broker.answer("yes") and not broker.answer("yes",confirm_id=shown[-1]["confirm_id"]))
    check("another page cannot answer the question even with its ID",not scoped_answer(client="page-b") and not task.done())
    check("an altered digest or operation cannot answer the question",not scoped_answer(digest="changed") and not scoped_answer(operation="bulk_undo"))
    check("the initiating page can answer its exact scope",scoped_answer() and await task)
    check("a decided approval cannot be replayed",not scoped_answer() and not broker.active and broker._scope is None)
    previous=shown[-1]["confirm_id"];task=await start_scoped()
    check("the next question rejects the previous confirmation ID",not scoped_answer(confirm_id=previous))
    broker.cancel_scoped("page-b")
    check("disconnecting another page leaves the question open",not task.done())
    broker.cancel_scoped("page-a")
    check("disconnecting the initiating page denies and clears approval",not await task and broker._scope is None)
    task=await start_scoped(seconds=.03)
    check("an unanswered scope expires without approval",not await task and broker._scope is None)
    broker._config=replace(broker._config,confirm=replace(broker._config.confirm,ask_first=False))
    count=len(shown)
    check("confirmations-off never answers a strict question automatically",not await broker.ask_scoped(scoped_send,operation="bulk_apply",digest="d",client="page-a",expires=time.monotonic()+1) and len(shown)==count and not rows)
    broker._config=replace(broker._config,confirm=replace(broker._config.confirm,ask_first=True))
    task=await start_scoped();broker._config=replace(broker._config,confirm=replace(broker._config.confirm,ask_first=False))
    check("turning confirmations off while a scope is pending denies its answer",not scoped_answer() and not await task)
    broker._config=replace(broker._config,confirm=replace(broker._config.confirm,ask_first=True))
    task=await start_scoped();task.cancel();await asyncio.gather(task,return_exceptions=True)
    check("cancelling a strict ask releases the broker for ordinary questions",broker._scope is None and not broker.active and not broker._lock.locked())
    print(f"\nall {len(CHECKS)} checks passed")


if __name__ == "__main__":
    asyncio.run(main())
