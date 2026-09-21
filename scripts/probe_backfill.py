"""Probe the hardening — what survives a hub that is away.

    uv run scripts/probe_backfill.py

The resilience the split deferred, driven with fakes: the spoke's timer
mirror (the hub's set synced, a local timer armed offline, what rings
and when — local at its time, the hub's only with the link down or past
the grace — the rung record that keeps a late delivery silent, persistence
across a restart, the offline cancel and listing); the hub's say-id
dedupe (a resend after a reconnect is acked, not queued twice); the
timers.sync broadcast deduped at the server; the away text on the hub
(iMessage through the spoke, and a send that fails holds the event
rather than losing it); and the doctor's checks with the CLI probe
stubbed. Nutrition and the doctor share the required-token rule, including
loopback, missing-token diagnostics, and the disabled state, without secrets.
"""

import asyncio
import json
import sys
import tempfile
from collections import deque
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ciel.config import Config, HubConfig, ProactiveConfig, WebConfig
from ciel.hub import doctor
from ciel.hub.server import HubServer
from ciel.messages import MessagesUnavailable
from ciel.pipeline import Pipeline
from ciel.remote.web import Admission
from ciel.spoke.timers import TimerMirror
from ciel.timers import Timer

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


def t(id, due_at, *, kind="timer", label="", duration_s=600.0, pending=False):
    return {"id": id, "kind": kind, "due_at": due_at, "label": label,
            "duration_s": duration_s, "pending": pending}


def probe_mirror() -> None:
    print("the timer mirror")
    tmp = Path(tempfile.mkdtemp())
    m = TimerMirror(tmp / "spoke-timers.json", grace_s=20.0)
    m.sync([t("t1", 1000.0), t("t2", 2000.0, kind="alarm"), t("t3", 900.0, pending=True)])
    check("a sync mirrors the hub's set, soonest first",
          [x.id for x in m.active(0.0)] == ["t3", "t1", "t2"])
    check("nothing is due before its time", m.due(999.0, hub_connected=True) == [])
    check("a due timer with the hub connected is the hub's to ring",
          m.due(1005.0, hub_connected=True) == [])
    check("with the hub down the spoke rings it", [x.id for x in m.due(1005.0, hub_connected=False)] == ["t1"])
    check("past the grace the spoke rings it even with the hub up (a wedged hub)",
          [x.id for x in m.due(1021.0, hub_connected=True)] == ["t1"])
    check("a pending timer never rings from the mirror",
          all(x.id != "t3" for x in m.due(5000.0, hub_connected=False)))
    m.mark_rung("t1", 1006.0)
    check("rung is remembered, and not due again",
          m.rung_already("t1") and m.due(1100.0, hub_connected=False) == [])
    m.sync([t("t2", 2000.0, kind="alarm")], now=1010.0)
    check("a sync that drops the rung timer keeps the rung record for a while",
          m.rung_already("t1") and [x.id for x in m.active(0.0)] == ["t2"])

    local = m.set_local(300.0, now=1500.0)
    check("an offline set_timer arms a local timer",
          local.id.startswith("local-") and local.due_at == 1800.0
          and [x.id for x in m.active(1500.0)] == ["t2", local.id] or
          [x.id for x in m.active(1500.0)] == [local.id, "t2"])
    check("a local timer rings at its time whatever the link",
          [x.id for x in m.due(1801.0, hub_connected=True)] == [local.id])
    check("the listing names both, hub's wording",
          m.listing(1500.0).startswith("Your 5 minute timer with 5 minutes left; an alarm at"))
    check("cancel with two running asks which", m.cancel(1500.0) is None)
    m2 = TimerMirror(tmp / "spoke-timers.json", grace_s=20.0)
    check("a restart reloads the mirror, local timer and rung record included",
          [x.id for x in m2.active(1500.0)] in ([local.id, "t2"], ["t2", local.id]) and m2.rung_already("t1"))
    m2.sync([], now=1500.0)
    check("the hub's set emptied: the local timer stays", [x.id for x in m2.active(1500.0)] == [local.id])
    cancelled = m2.cancel(1500.0)
    check("cancel with one running cancels it, and it is gone",
          cancelled is not None and cancelled.id == local.id and m2.active(1500.0) == [])
    check("the empty listing", m2.listing(0.0) == "No timers are running.")
    m2.sync([t("t9", 3000.0)], now=2400.0)
    check("cancelling the hub's own timer marks it so the mirror never rings it",
          m2.cancel(2500.0) is not None and m2.due(3100.0, hub_connected=False) == [])


def probe_say_dedupe() -> None:
    print("\nthe say-id dedupe and the timers.sync broadcast")
    server = HubServer(WebConfig(), HubConfig())
    q, _ = server._welcome("spoke", Admission(True, role="spoke", client_id="mac"))
    q.get_nowait()
    frame = {"type": "say", "text": "set a timer", "seq": 1, "say_id": "abc"}
    server._on_frame(json.dumps(frame), "spoke")
    server._on_frame(json.dumps({**frame, "seq": 2}), "spoke")
    acks = [json.loads(q.get_nowait())["seq"] for _ in range(q.qsize())]
    check("a resent say is acked both times but queued once",
          acks == [1, 2] and server.pop_voice()[1] == "set a timer" and not server.voice_pending)
    server._on_frame(json.dumps({"type": "say", "text": "again", "seq": 3}), "spoke")
    check("a say without an id (an older spoke) still queues", server.voice_pending)
    server.pop_voice()

    timers = [t("t1", 1000.0)]
    server.note_timers(timers)
    server.note_timers([dict(x) for x in timers])
    frames = [json.loads(q.get_nowait()) for _ in range(q.qsize())]
    syncs = [f for f in frames if f["type"] == "timers.sync"]
    check("timers.sync is broadcast once for an unchanged set, with a seq",
          len(syncs) == 1 and syncs[0]["timers"][0]["id"] == "t1" and "seq" in syncs[0])
    server.note_timers([])
    check("and again when it changes", json.loads(q.get_nowait())["timers"] == [])


class FakeOwner:
    """The Messages client as the hub sees it: the RPC binding raises
    MessagesUnavailable when no spoke is seated to carry the send."""

    def __init__(self, seated=True):
        self.sent = []
        self.seated = seated

    async def send(self, handle, text):
        if not self.seated:
            raise MessagesUnavailable("no spoke seated")
        self.sent.append((handle, text))


class FakeEvents:
    def __init__(self):
        self.messaged = []
        self.held = []

    def mark_messaged(self, event, now, day):
        self.messaged.append(event.id)

    def hold(self, event):
        self.held.append(event.id)

    def take_held(self, now):
        return []


class FakeEvent:
    id = "ev1"
    source = "calendar"
    summary = "Your meeting is close."


async def probe_away_ladder() -> None:
    print("\nthe away text on the hub")
    cfg = replace(Config(), proactive=replace(ProactiveConfig(), owner_handle="+15550000001"))

    def make(spoke_seated: bool):
        p = Pipeline.__new__(Pipeline)
        p._config = cfg
        p._role = "hub"
        server = HubServer(WebConfig(), HubConfig())
        if spoke_seated:
            server._welcome("spoke", Admission(True, role="spoke"))
        p._web_link = server
        p._events = FakeEvents()
        p._owner_messages = FakeOwner(seated=spoke_seated)
        p._transcript = None
        p._presence = None
        p._calendar = None

        async def compose(event, *, outlet, fallback=None):
            return "Heads up."

        p._compose_unattended = compose
        return p

    p = make(spoke_seated=True)
    await p._run_proactive_message(FakeEvent())
    check("spoke seated: the text goes through the Mac's iMessage",
          p._owner_messages.sent == [("+15550000001", "Heads up.")]
          and p._events.messaged == ["ev1"])
    p = make(spoke_seated=False)
    await p._run_proactive_message(FakeEvent())
    check("spoke away: the send fails and the event is held, not lost",
          p._owner_messages.sent == [] and p._events.messaged == []
          and p._events.held == ["ev1"])


def _probe_doctor() -> None:
    print("\nthe doctor")
    tmp = Path(tempfile.mkdtemp())
    doctor._cli_auth = lambda config: (True, "stubbed")
    cfg = replace(Config(), state_dir=tmp / "absent")
    rows = {r[0]: r for r in doctor.run(cfg)}
    check("loopback: bind and token pass without a token",
          rows["bind"][1] and rows["token"][1])
    check("a missing state dir fails the state check", not rows["state"][1])
    cfg = replace(Config(), state_dir=tmp)
    cfg2 = replace(cfg, hub=replace(HubConfig(), bind="203.0.113.7", token="",
                                    token_file=tmp / "no-such-token"))
    rows = {r[0]: r for r in doctor.run(cfg2)}
    check("an address this machine lacks fails the bind check",
          not rows["bind"][1] and "NOT an address" in rows["bind"][2])
    check("no token off loopback is reported, with where it will be minted",
          not rows["token"][1] and "mints" in rows["token"][2])
    cfg3 = replace(cfg, hub=replace(HubConfig(), bind="127.0.0.1", token="x"))
    check("every row carries a name, a verdict, and a sentence",
          all(isinstance(r[0], str) and isinstance(r[1], bool) and r[2] for r in doctor.run(cfg3)))


def probe_doctor() -> None:
    with tempfile.TemporaryDirectory() as tmp, patch.object(Path, "home", return_value=Path(tmp)):
        _probe_doctor()
        cfg = Config()
        cfg = replace(cfg, state_dir=Path(tmp), hub=replace(cfg.hub, require_token=True, token="fixture-token"))
        from ciel.nutrition import readiness
        with patch("ciel.nutrition.socket.gethostname", return_value="fixture-host"):
            cfg = replace(cfg, nutrition=replace(cfg.nutrition, enabled=True, owner_host="fixture-host"))
            rows = {name:(ok,text) for name,ok,text in doctor.run(cfg)}
            check("nutrition and the doctor share the token readiness rule", rows["nutrition"] == readiness(cfg))
            check("loopback diagnostics say when a token is required", rows["token"][0] and "required" in rows["token"][1] and "no token needed" not in rows["bind"][1])
            missing = replace(cfg, hub=replace(cfg.hub, token=""))
            missing_rows = {name:(ok,text) for name,ok,text in doctor.run(missing)}
            check("missing required tokens fail nutrition even on loopback", not missing_rows["nutrition"][0] and not missing_rows["token"][0])
            check("nutrition diagnostics never print the token value", "fixture-token" not in str(rows))
            bad = replace(cfg,nutrition=replace(cfg.nutrition,photo_max_bytes=3000000))
            check("the doctor and runtime both refuse photo bounds above the wire ceiling",not readiness(bad)[0] and ("nutrition",*readiness(bad)) in doctor.run(bad))
            bad = replace(cfg,nutrition=replace(cfg.nutrition,bulk_max_meals=101))
            check("the doctor and runtime refuse unbounded historical reviews",not readiness(bad)[0] and ("nutrition",*readiness(bad)) in doctor.run(bad))
            bad = replace(cfg,nutrition=replace(cfg.nutrition,dashboard_max_days=367))
            check("the doctor and runtime refuse unbounded dashboard reads",not readiness(bad)[0] and ("nutrition",*readiness(bad)) in doctor.run(bad))
            off = replace(cfg, nutrition=replace(cfg.nutrition, enabled=False))
            check("nutrition off is explicitly reported as off", ("nutrition", True, "off") in doctor.run(off))


async def main() -> None:
    probe_mirror()
    probe_say_dedupe()
    await probe_away_ladder()
    probe_doctor()
    print(f"\nall {len(CHECKS)} checks passed")


if __name__ == "__main__":
    asyncio.run(main())
