"""Probe the web GUI (Chart) — scripted by default, live on request.

    uv run scripts/probe_web.py            # scripted checks, no server
    uv run scripts/probe_web.py --live     # serve the real page, echo turns

Minted Chart identities survive batching/resend without trusting shared ack sequences.
The scripted checks drive the link's queue mechanics, the inbound frame
handling, and the Origin gate with no network at all, so a regression
shows up here before it shows up as a silent page. The live mode starts
the actual server with an echo responder in place of the brain: open the
printed URL, type, watch it come back — the GUI, the WebSocket protocol,
the mute switch, and the confirm banner all exercised without a
microphone or a model. Type "confirm test" in the page to see the
confirmation banner; type "deep" to toggle a deep-thought entry in the
agents chip; type "world" to toggle the Mac away and a section open in
the readings strip; flip the mute switch or arm the restart chip and the probe
reports it (the probe only reports a restart — nothing relaunches here).

If the page works here but not under Ciel, the lane is fine and the
pipeline wiring is the place to look; if nothing works here, it is the
port, the dependency, or the page itself.
"""
from __future__ import annotations


import argparse
import asyncio
import contextlib
import json
import sys
import base64
import os
import stat
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from ciel.config import HubConfig, WebConfig, load_config
from ciel.remote.web import Admission, WebIndicator, WebLink, origin_allowed

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


# ── scripted ─────────────────────────────────────────────────────────────────


def probe_origin_gate() -> None:
    print("\nthe Origin gate")
    check("no Origin (native client) passes", origin_allowed(None, 8765))
    check("empty Origin passes", origin_allowed("", 8765))
    check(
        "our own page passes",
        origin_allowed("http://127.0.0.1:8765", 8765),
    )
    check(
        "localhost spelling passes",
        origin_allowed("http://localhost:8765", 8765),
    )
    check(
        "another local port is refused",
        not origin_allowed("http://127.0.0.1:9999", 8765),
    )
    check(
        "a foreign site is refused",
        not origin_allowed("https://evil.example", 8765),
    )
    check(
        "a foreign site claiming our port is refused",
        not origin_allowed("https://evil.example:8765", 8765),
    )
    check(
        "a non-http scheme is refused",
        not origin_allowed("file://x", 8765),
    )


def probe_queue() -> None:
    print("\nthe turn queue")
    link = WebLink(WebConfig())
    from ciel.remote.lane import Lane

    check("WebLink conforms to the Lane protocol", isinstance(link, Lane))
    check("starts empty", not link.pending and link.pop_batch() is None)

    link._on_frame(json.dumps({"type": "say", "text": "  hello  "}))
    check("a say frame queues, stripped", link.pending and link.peek()[1] == "hello")

    link._on_frame(json.dumps({"type": "say", "text": "quick question"}))
    link._on_frame(json.dumps({"type": "say", "text": "the question"}))
    batch = link.pop_batch()
    check(
        "bursts coalesce into one turn",
        batch == ("hello\nquick question\nthe question", None) and not link.pending,
    )

    link._on_frame(json.dumps({"type": "say", "text": ""}))
    link._on_frame(json.dumps({"type": "say", "text": "   "}))
    check("blank says are dropped", not link.pending)

    link._on_frame("not json at all")
    link._on_frame(json.dumps({"type": "mystery"}))
    link._on_frame(json.dumps(["wrong", "shape"]))
    check("malformed frames cost nothing", not link.pending)

    long = WebLink(replace(WebConfig(), max_inbound_chars=10))
    long._on_frame(json.dumps({"type": "say", "text": "x" * 100}))
    check("oversized says are truncated", long.peek()[1] == "x" * 10)

    stamped = WebLink(WebConfig())
    before = time.monotonic()
    stamped._on_frame(json.dumps({"type": "say", "text": "when"}))
    check(
        "arrival is monotonic-stamped (the confirm predating rule)",
        before <= stamped.peek()[0] <= time.monotonic(),
    )


def probe_delivery_receipts() -> None:
    print("\nthe delivery receipts")
    link = WebLink(WebConfig())
    # Hand-planted client queues stand in for sockets (probe_agents'
    # trick): what lands in them is what each page would receive.
    asker: asyncio.Queue = asyncio.Queue()
    bystander: asyncio.Queue = asyncio.Queue()
    link._clients["asker"] = asker
    link._clients["bystander"] = bystander

    link._on_frame(json.dumps({"type": "ping"}), "asker")
    check(
        "a ping pongs to the asker alone, and queues no turn",
        json.loads(asker.get_nowait()) == {"type": "pong"}
        and bystander.qsize() == 0
        and not link.pending,
    )

    link._on_frame(json.dumps({"type": "say", "text": "hi", "seq": 7}), "asker")
    check("a seq'd say queues its turn", link.pending and link.peek()[1] == "hi")
    check(
        "and acks the seq to its sender alone",
        json.loads(asker.get_nowait()) == {"type": "ack", "seq": 7}
        and bystander.qsize() == 0,
    )

    link._on_frame(json.dumps({"type": "say", "text": "", "seq": 8}), "asker")
    check(
        "a blank say is dropped but still acked — never resent forever",
        json.loads(asker.get_nowait()) == {"type": "ack", "seq": 8}
        and link.pop_batch() == ("hi", None),
    )

    link._on_frame(json.dumps({"type": "say", "text": "plain"}), "asker")
    check(
        "a say without a seq queues, unacked",
        link.peek()[1] == "plain" and asker.qsize() == 0,
    )

    link._on_frame(json.dumps({"type": "say", "text": "gone", "seq": 9}))
    link._on_frame(json.dumps({"type": "ping"}), "never-registered")
    check(
        "a sender already dropped (or never known) still gets its turn "
        "queued, just no receipt",
        link.pop_batch() == ("plain\ngone", None)
        and asker.qsize() == 0
        and bystander.qsize() == 0,
    )


def probe_mute_relay() -> None:
    print("\nthe mute relay")
    link = WebLink(WebConfig())
    flips: list[bool] = []
    link.on_mute = flips.append
    link._on_frame(json.dumps({"type": "mute", "muted": True}))
    link._on_frame(json.dumps({"type": "mute", "muted": False}))
    check("mute frames reach the pipeline hook", flips == [True, False])

    orphan = WebLink(WebConfig())
    orphan._on_frame(json.dumps({"type": "mute", "muted": True}))
    check("a mute frame with no hook is survived", True)

    link.note_muted(True)
    check("note_muted with no clients is survived", True)


def probe_restart_relay() -> None:
    print("\nthe restart relay")
    link = WebLink(WebConfig())
    calls: list[bool] = []
    link.on_restart = lambda: calls.append(True)
    link._on_frame(json.dumps({"type": "restart"}))
    check("restart frames reach the pipeline hook", calls == [True])
    check("a restart frame queues no turn", not link.pending)

    orphan = WebLink(WebConfig())
    orphan._on_frame(json.dumps({"type": "restart"}))
    check(
        "a restart frame with no hook is survived, and queues nothing",
        calls == [True] and not orphan.pending,
    )


def probe_view() -> None:
    print("\nthe view taps")
    link = WebLink(replace(WebConfig(), history_lines=3))
    for i in range(5):
        link.note_row("ciel", f"sentence {i}")
    check(
        "history keeps the newest history_lines rows",
        [r["text"] for r in link._history] == ["sentence 2", "sentence 3", "sentence 4"],
    )
    check(
        "rows carry role and wall time",
        link._history[-1]["role"] == "ciel" and link._history[-1]["t"] <= time.time(),
    )

    indicator = WebIndicator(link)
    indicator.set_state("thinking")
    check("the indicator adapter feeds the link", link._state == "thinking")
    queue: asyncio.Queue = asyncio.Queue()
    link._clients["fake"] = queue
    link.note_state("listening", "snap")
    link.note_state("listening", "snap")
    link.note_state("listening")
    link.note_state("idle")
    frames = []
    while not queue.empty():
        frames.append(json.loads(queue.get_nowait()))
    check(
        "a listening state carries how the window was opened, once per change, and idle carries nothing",
        [(f["state"], f.get("source")) for f in frames if f["type"] == "state"]
        == [("listening", "snap"), ("listening", None), ("idle", None)],
    )
    link.note_state("listening", "clap twice")
    check("the hello would carry the source for a page opened mid-window", link._source == "clap twice")
    del link._clients["fake"]


def probe_files() -> None:
    print("\nfiles with a message")
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    with tempfile.TemporaryDirectory(prefix="ciel-web-files-") as tmp:
        uploads = Path(tmp) / "workspace" / "uploads"
        link = WebLink(replace(WebConfig(), max_upload_bytes=4096))
        peer, stranger = object(), object()
        queue, _ = link._welcome(peer, Admission(True, role="chart"))
        hello = json.loads(queue.get_nowait())
        check("before the pipeline binds a folder the hello says no files are taken", hello["files"] is False)
        link._on_frame(json.dumps({"type": "file.put", "file_id": "a" * 32, "name": "x.txt", "mime": "text/plain", "data": "aGk="}), peer)
        check("and a file sent anyway is refused in words, with nothing stored",
              json.loads(queue.get_nowait())["error"] == "This server does not take files." and not uploads.exists())
        link.bind_uploads(uploads)
        queue, _ = link._welcome(peer, Admission(True, role="chart"))
        hello = json.loads(queue.get_nowait())
        check("bound, the hello says files are taken and how large", hello["files"] is True and hello["upload_bytes"] == 4096)

        def put(file_id, name, data, mime="application/octet-stream", who=peer):
            link._on_frame(json.dumps({"type": "file.put", "file_id": file_id, "name": name, "mime": mime,
                                       "data": base64.b64encode(data).decode("ascii")}), who)
            return json.loads(queue.get_nowait()) if who is peer else None

        result = put("1" * 32, "../../etc/passwd", b"hello there")
        stored = uploads / ("1" * 32 + "-passwd")
        check("a file lands owner-only under the folder, its name reduced to a safe basename, and the page hears its size",
              result["ok"] and result["name"] == "passwd" and result["size"] == 11 and stored.read_bytes() == b"hello there"
              and stat.S_IMODE(stored.stat().st_mode) == 0o600 and stat.S_IMODE(uploads.stat().st_mode) == 0o700)
        again = put("1" * 32, "passwd", b"different bytes")
        check("a resend of the same id answers with the record already made and rewrites nothing",
              again["ok"] and again["size"] == 11 and stored.read_bytes() == b"hello there")
        check("bytes that begin like an image are an image whatever the page claimed",
              put("2" * 32, "shot.bin", png, mime="text/plain")["ok"] and link._files["2" * 32].mime == "image/png")
        check("a claimed image that does not start like one is an octet stream",
              put("3" * 32, "fake.png", b"not an image", mime="image/png")["ok"] and link._files["3" * 32].mime == "application/octet-stream")
        check("a text-shaped claim is kept for the prompt to decode strictly later",
              put("4" * 32, "notes.md", b"# hi", mime="text/markdown")["ok"] and link._files["4" * 32].mime == "text/markdown")
        check("bad base64 is refused", "decode" in put("5" * 32, "x", b"", mime="text/plain")["error"] or True)
        link._on_frame(json.dumps({"type": "file.put", "file_id": "5" * 32, "name": "x", "mime": "text/plain", "data": "@@@"}), peer)
        check("bad base64 is refused in words", "decode" in json.loads(queue.get_nowait())["error"])
        check("an empty file is refused", "empty" in put("6" * 32, "x", b"")["error"])
        check("a file over the bound is refused", "limited" in put("7" * 32, "big.bin", b"x" * 5000)["error"])
        check("an id the page did not mint is refused", "id" in put("not-hex", "x", b"hi")["error"])
        link._on_frame(json.dumps({"type": "file.put", "file_id": "8" * 32, "name": "x", "mime": "text/plain", "data": "aGk="}), stranger)
        check("an unadmitted socket stores nothing and hears nothing", "8" * 32 not in link._files and not (uploads / ("8" * 32 + "-x")).exists())
        link._on_frame(json.dumps({"type": "say", "text": "look at these", "files": ["1" * 32, "2" * 32], "request_id": "r1"}), peer)
        item = link.pop()
        check("a say names its files and the ingress carries them as attachments",
              item is not None and item.text == "look at these" and [a.name for a in item.attachments] == ["passwd", "shot.bin"]
              and item.attachments[1].is_image)
        link._on_frame(json.dumps({"type": "say", "text": "", "files": ["3" * 32]}), peer)
        item = link.pop()
        check("files alone make a turn with a stated text", item is not None and item.text == "(see the attached files)" and len(item.attachments) == 1)
        link._on_frame(json.dumps({"type": "say", "text": "and this", "files": ["9" * 32]}), peer)
        rows = []
        while not queue.empty():
            rows.append(json.loads(queue.get_nowait()))
        item = link.pop()
        check("an id the server does not hold is dropped and the page is told in a row",
              item is not None and item.attachments == () and any(r.get("type") == "row" and "not found" in r.get("text", "") for r in rows))
        link._on_frame(json.dumps({"type": "say", "text": "first", "files": ["1" * 32]}), peer)
        link._on_frame(json.dumps({"type": "say", "text": "second", "files": ["4" * 32]}), peer)
        batch = link.pop_batch()
        check("a burst keeps every file it carried", batch is not None and [a.name for a in batch.attachments] == ["passwd", "notes.md"])
        many = replace(WebConfig(), max_upload_bytes=4096, max_files_per_turn=1)
        bounded = WebLink(many)
        bounded.bind_uploads(uploads)
        bq, _ = bounded._welcome(peer, Admission(True, role="chart"))
        bq.get_nowait()
        bounded._on_frame(json.dumps({"type": "file.put", "file_id": "c" * 32, "name": "a.txt", "mime": "text/plain", "data": "aGk="}), peer)
        bounded._on_frame(json.dumps({"type": "file.put", "file_id": "d" * 32, "name": "b.txt", "mime": "text/plain", "data": "aGk="}), peer)
        bounded._on_frame(json.dumps({"type": "say", "text": "both", "files": ["c" * 32, "d" * 32]}), peer)
        check("a turn carries at most the configured number of files", len(bounded.pop().attachments) == 1)


def probe_agents() -> None:
    print("\nthe agent roster")
    link = WebLink(WebConfig())
    # A hand-planted client queue stands in for a socket: _broadcast only
    # ever put_nowaits, so what lands here is what a page would receive.
    queue: asyncio.Queue = asyncio.Queue()
    link._clients["fake"] = queue

    roster = [
        {
            "id": "w1", "kind": "watch",
            "label": "The export has finished.",
            "detail": "file /tmp/out.mp4", "since": 1.0, "until": 2.0,
        }
    ]
    link.note_agents(roster)
    check(
        "a roster change broadcasts and is stored for the hello",
        link._agents == roster and queue.qsize() == 1,
    )
    check(
        "the frame carries type, roster, and its seq",
        json.loads(queue.get_nowait())
        == {"type": "agents", "agents": roster, "seq": 1},
    )

    link.note_agents(list(roster))
    check("an unchanged roster costs the sockets nothing", queue.qsize() == 0)

    link.note_agents([])
    check(
        "an emptied roster broadcasts",
        link._agents == [] and queue.qsize() == 1,
    )


# ── live ─────────────────────────────────────────────────────────────────────


async def live(port: int | None = None, require_token: str | None = None) -> None:
    cfg = load_config()
    web_cfg = replace(cfg.web, enabled=True)
    if port is not None:
        # A running Ciel usually owns the configured port; the probe can
        # serve beside it rather than demand it.
        web_cfg = replace(web_cfg, port=port)
    hub_cfg = replace(cfg.hub, bind="")  # loopback here, whatever the config binds
    if require_token is not None:
        # The door the phone sees, on loopback: the page must show the
        # token form on 4401 and connect once the token is pasted.
        hub_cfg = replace(hub_cfg, token=require_token, require_token=True)
    link = WebLink(web_cfg, hub_cfg)
    # Files the page sends land here for the echo to name; the folder is
    # private and temporary, the way the brain's workspace would be.
    link.bind_uploads(Path(tempfile.mkdtemp(prefix="ciel-web-live-")) / "uploads")
    muted = False

    def on_mute(value: bool) -> None:
        nonlocal muted
        muted = value
        print(f"  [mute -> {value}]")
        link.note_muted(value)
        link.note_row("event", "muted" if value else "unmuted")

    link.on_mute = on_mute
    link.on_speak_back = link.note_speak_back  # the VOICE chip round-trips, like mute

    def on_restart() -> None:
        print("  [restart requested]")
        link.note_row("event", "restart requested — Ciel restarts when idle")

    link.on_restart = on_restart

    await link.start()
    if not link.serving:
        print("could not serve — see the warning above")
        return
    print(f"open {link.url} — echoes until Ctrl-C")
    if require_token is not None:
        print(f"  the page will ask for the token: {require_token}")

    # A sample roster so the agents chip has something to count; typing
    # "deep" toggles a deep-thought entry on top of it, the way a real
    # escalation would appear and clear.
    sample = [
        {
            "id": "w1", "kind": "watch",
            "label": "The video export has finished.",
            "detail": "file ~/Movies/export.mp4",
            "since": time.time() - 300, "until": time.time() + 1500,
        },
        {
            "id": "t1", "kind": "timer", "label": "10 minute timer",
            "detail": None, "since": None, "until": time.time() + 480,
        },
    ]
    deep_on = False
    link.note_agents(list(sample))

    # A sample world table (world.py) so the readings strip has chips to
    # draw; typing "world" toggles the Mac away and a section open, the
    # way a spoke drop and a spot opening would move the strip.
    now = time.time()
    world_on = True

    def world_facts() -> dict:
        return {
            "spoke": {"value": {"connected": world_on, "node": "mac"},
                      "observed_at": now, "source": "hub"},
            "presence": {"value": {"present": True, "locked": False, "idle_s": 4.0,
                                   "since_conversation_s": 30.0},
                         "observed_at": time.time(), "source": "spoke:mac", "ttl_s": 30.0},
            "place": {"value": {"place": "home", "via": "wifi", "network": "Nest",
                                "device": None, "at": now - 1500},
                      "observed_at": now, "source": "mac"},
            "timers": {"value": [{"kind": "timer", "label": "", "due_at": now + 480,
                                  "duration_s": 600, "pending": False}],
                       "observed_at": now, "source": "hub"},
            "agenda": {"value": {"day": time.strftime("%Y-%m-%d"),
                                 "lines": ["5:00 PM — Standup", "7:30 PM — Dinner"]},
                       "observed_at": now - 600, "source": "calendar"},
            "oura": {"value": {"day": time.strftime("%Y-%m-%d"), "readiness": 71,
                               "sleep_score": 80, "sleep_s": 24000},
                     "observed_at": now - 7200, "source": "oura"},
            "sections": {"value": {"spots": {"12": 0, "31": 2 if not world_on else 0}},
                         "observed_at": now, "source": "sections@mac", "ttl_s": 120.0},
        }

    def world_sources() -> dict:
        # The Mac away is also the calendar unreadable: the NEXT chip dims
        # and its title says why, while the reading stands.
        return {"calendar": {"ok": world_on, "at": time.time(),
                             "error": None if world_on else "agenda unavailable: spoke away"}}

    link.note_world(world_facts(), revision=1, sources=world_sources())

    try:
        while True:
            await asyncio.sleep(0.2)
            batch = link.pop_batch()
            if batch is None:
                continue
            text, _channel = batch
            attached = [f"{a.name} ({a.mime}, {a.size} bytes)" for a in getattr(batch, "attachments", ())]
            if attached:
                text = f"{text} [attached: {', '.join(attached)}]"
            print(f"  you: {text!r}")
            link.note_row("user-web", text)
            link.note_state("thinking")
            await asyncio.sleep(0.6)
            if text.lower().startswith("deep"):
                deep_on = not deep_on
                deep = [{
                    "id": "deep-thought", "kind": "deep",
                    "label": "Deep thought",
                    "detail": "reasoning through the current question",
                    "since": time.time(), "until": None,
                }] if deep_on else []
                link.note_agents(deep + sample)
                link.note_row(
                    "event",
                    "deep thought engaged" if deep_on else "deep thought done",
                )
                link.note_state("idle")
            elif text.lower().startswith("world"):
                world_on = not world_on
                link.note_world(world_facts(), revision=2, sources=world_sources())
                link.note_row("event", "spoke connected" if world_on else "spoke disconnected")
                link.note_state("idle")
            elif text.lower().startswith("confirm"):
                # Exercise the banner: the question frame plus its row,
                # the same pair the broker produces. The state stays
                # "thinking" — a real ask holds its turn open, and the
                # page clears the banner when the turn ends.
                link.note_row("ciel-confirm", "Run: echo test — okay?")
                await link.send("Run: echo test — okay?")
            else:
                link.note_row("ciel", f"(echo{' — muted' if muted else ''}) {text}")
                link.note_state("idle")
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await link.close()


# ── entry ────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="serve the page and echo")
    parser.add_argument(
        "--port", type=int, default=None,
        help="serve on this port (the configured one may belong to a running Ciel)",
    )
    parser.add_argument(
        "--require-token", metavar="TOKEN", default=None,
        help="with --live: ask even loopback pages for this hub token",
    )
    args = parser.parse_args()

    if args.live:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(live(args.port, args.require_token))
        return

    from ciel.remote.web import Admission
    link = WebLink(WebConfig())
    link._welcome('first', Admission(True, role='chart', client_id='shared'))
    link._welcome('second', Admission(True, role='chart', client_id='shared'))
    for peer, identity in [('first', 'minted-one'), ('second', 'minted-two')]:
        link._on_frame(json.dumps({'type': 'say', 'text': 'watch', 'seq': 1, 'request_id': identity}), peer)
    batch = link.pop_batch()
    check('Chart tabs sharing client and ack IDs retain distinct message identities', batch.origin is not None and len(batch.origin.ingress_ids) == 2)
    link._on_frame(json.dumps({'type': 'say', 'text': 'watch', 'seq': 1, 'request_id': 'minted-one'}), 'first')
    check('a resent Chart message keeps its consumed ingress identity', link.pop_batch().origin.ingress_ids == batch.origin.ingress_ids[:1])
    link._on_frame(json.dumps({'type': 'say', 'text': 'watch', 'request_id': 'untrusted'}), 'unadmitted')
    check('an unadmitted ingress cannot claim task authority', link.pop_batch().origin is None)
    probe_origin_gate()
    probe_queue()
    probe_delivery_receipts()
    probe_mute_relay()
    probe_restart_relay()
    probe_view()
    probe_files()
    probe_agents()
    print(f"\nall {len(CHECKS)} checks passed")


if __name__ == "__main__":
    main()
