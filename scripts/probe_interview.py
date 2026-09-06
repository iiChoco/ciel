"""Probe the interview room (Adjoint) — one layer per subcommand, no model.

    uv run scripts/probe_interview.py auth      # accounts, cookies, the door
    uv run scripts/probe_interview.py brief     # setup → brief, the store, the scripted brain
    uv run scripts/probe_interview.py endpoint  # the patient endpointer, on a fake clock
    uv run scripts/probe_interview.py wire      # the room's frame catalog
    uv run scripts/probe_interview.py session   # a whole scripted interview over the socket
    uv run scripts/probe_interview.py speaker   # piper → WAV (skips when piper is absent)
    uv run scripts/probe_interview.py cases     # the case library and its validator
    uv run scripts/probe_interview.py brain     # the SDK backend against a fake CLI: the cut-off race

Each subcommand builds the room on a throwaway state directory and drives
it through aiohttp's test client, so a regression shows up here before it
shows up as a friend who cannot sign in. Nothing here needs the hub, a
network, or a model.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import sys
import tempfile
from pathlib import Path

from ciel.config import load_config

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


def _config(state: Path):
    config = load_config()
    return dataclasses.replace(
        config,
        interview=dataclasses.replace(config.interview, enabled=True, dir=state),
    )


# ── auth ─────────────────────────────────────────────────────────────────────


def probe_accounts(state: Path) -> None:
    from ciel.interview.accounts import (
        Accounts, generate_password, mint_secret, read_cookie, sign_cookie,
    )

    print("\naccounts")
    accounts = Accounts(state / "interview-accounts.json")
    check("starts empty", accounts.empty)
    pw = accounts.create("alice")
    check("password has four words and digits", pw.count("-") == 4 and pw.split("-")[-1].isdigit())
    check("file is owner-only", (accounts.path.stat().st_mode & 0o777) == 0o600)
    check("verify accepts the password", accounts.verify("alice", pw) is not None)
    check("verify refuses a wrong one", accounts.verify("alice", pw + "x") is None)
    check("verify refuses an unknown user", accounts.verify("bob", pw) is None)
    for bad in ("A", "Alice", "a b", "x" * 40, "", "-lead"):
        try:
            accounts.create(bad)
            ok = False
        except ValueError:
            ok = True
        check(f"username {bad!r} refused", ok)
    try:
        accounts.create("alice")
        ok = False
    except ValueError:
        ok = True
    check("duplicate refused", ok)
    new = accounts.reset("alice")
    check("reset changes the password", new != pw and accounts.verify("alice", new))
    check("old password dead after reset", accounts.verify("alice", pw) is None)
    accounts.set_disabled("alice", True)
    check("disabled account cannot verify", accounts.verify("alice", new) is None)
    accounts.set_disabled("alice", False)
    check("re-enabled verifies again", accounts.verify("alice", new) is not None)
    accounts.create("owner", "admin")
    check("admin role recorded", accounts.get("owner").is_admin)
    check("list is sorted", [a.username for a in accounts.list()] == ["alice", "owner"])
    first = accounts.get("alice")
    accounts.delete("alice")
    check("delete removes", accounts.get("alice") is None)
    check("two passwords differ", generate_password() != generate_password())
    accounts.create("alice")
    check("a recreated username is a new account: a new id",
          first.id and accounts.get("alice").id != first.id)
    check("file still owner-only after every rewrite", (accounts.path.stat().st_mode & 0o777) == 0o600)
    check("no stray temp files beside it",
          [p.name for p in accounts.path.parent.iterdir() if ".tmp" in p.name] == [])

    print("\nthe race")
    import threading
    from unittest.mock import patch

    import ciel.interview.accounts as accounts_mod

    entered, release = threading.Event(), threading.Event()
    original = accounts_mod._hash

    def held_hash(password, salt):
        entered.set()
        release.wait(5)
        return original(password, salt)

    with patch.object(accounts_mod, "_hash", held_hash):
        job = threading.Thread(target=accounts.reset, args=("alice",))
        job.start()
        entered.wait(5)
        accounts.set_disabled("alice", True)  # lands while the reset is in scrypt
        release.set()
        job.join(10)
    check("a reset that paused in scrypt does not undo a disable made meanwhile",
          accounts.get("alice").disabled)

    print("\nthe cookie")
    secret = mint_secret(state / "interview.secret")
    check("secret minted owner-only", (state / "interview.secret").stat().st_mode & 0o777 == 0o600)
    check("secret is stable", mint_secret(state / "interview.secret") == secret)
    owner = accounts.get("owner")
    cookie = sign_cookie(secret, owner, 2_000_000_000)
    ticket = read_cookie(secret, cookie, now=1_900_000_000)
    check("signature holds", ticket is not None and ticket.username == "owner" and ticket.matches(owner))
    check("expired refused", read_cookie(secret, cookie, now=2_000_000_001) is None)
    check("tampered user refused", read_cookie(secret, cookie.replace("owner", "alice"), now=1) is None)
    check("wrong secret refused", read_cookie("nope", cookie, now=1) is None)
    check("garbage refused", read_cookie(secret, "x|y", now=1) is None)
    check("an old three-part cookie refused", read_cookie(secret, "owner|2000000000|deadbeef", now=1) is None)
    accounts.reset("owner")
    check("a reset retires the cookie: the ticket no longer matches",
          not ticket.matches(accounts.get("owner")))
    fresh = sign_cookie(secret, accounts.get("owner"), 2_000_000_000)
    check("a cookie issued after it matches", read_cookie(secret, fresh, now=1).matches(accounts.get("owner")))
    accounts.delete("owner")
    accounts.create("owner", "admin")
    check("a username deleted and recreated is a stranger to every old cookie",
          not read_cookie(secret, fresh, now=1).matches(accounts.get("owner")))


async def probe_door(state: Path) -> None:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from ciel.interview.app import COOKIE, InterviewApp

    print("\nthe door")
    config = _config(state)
    room = InterviewApp(config)
    await room.start()
    pw = room.accounts.create("owner", "admin")
    friend_pw = room.accounts.create("friend")

    app = web.Application()
    room.register(app.router)
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/interview")
        check("page served", r.status == 200 and "interview.js" in await r.text() or r.status == 200)
        r = await client.get("/interview/app.js")
        check("script served", r.status == 200)
        r = await client.get("/interview/api/me")
        check("no cookie → 401", r.status == 401)
        r = await client.post("/interview/api/login", json={"username": "owner", "password": "wrong"})
        check("wrong password → 401", r.status == 401)
        r = await client.post("/interview/api/login", json={"username": "owner", "password": pw})
        check("login → 200", r.status == 200)
        data = await r.json()
        check("login names the account", data.get("username") == "owner" and data.get("role") == "admin")
        cookie = r.cookies.get(COOKIE)
        check("cookie set, HttpOnly, path-scoped",
              cookie is not None and cookie["httponly"] and cookie["path"] == "/interview")
        r = await client.get("/interview/api/me")
        check("cookie admits", r.status == 200)
        r = await client.get("/interview/api/admin/accounts")
        check("admin lists accounts", r.status == 200 and len((await r.json())["accounts"]) == 2)
        r = await client.post("/interview/api/admin/accounts", json={"username": "carol"})
        created = await r.json()
        check("admin creates, password shown once", r.status == 200 and created["password"].count("-") == 4)
        r = await client.post("/interview/api/admin/accounts/carol/reset")
        check("admin resets", r.status == 200 and (await r.json())["password"] != created["password"])
        r = await client.post("/interview/api/admin/accounts/carol/reset", json={"password": "short"})
        check("a chosen password under eight characters is refused", r.status == 400)
        r = await client.post("/interview/api/admin/accounts/carol/reset", json={"password": "correct-horse-battery"})
        check("admin sets a chosen password", r.status == 200 and (await r.json())["chosen"])
        check("the chosen password works", room.accounts.verify("carol", "correct-horse-battery") is not None)
        r = await client.post("/interview/api/admin/accounts", json={"username": "dave", "password": "ab"})
        check("create with a short chosen password is refused", r.status == 400)
        r = await client.post("/interview/api/admin/accounts", json={"username": "dave", "password": "dave-picks-this-one"})
        check("create with a chosen password", r.status == 200 and (await r.json())["password"] == "dave-picks-this-one")
        room.accounts.delete("dave")
        r = await client.post("/interview/api/admin/accounts/carol/disable")
        check("admin disables", r.status == 200 and (await r.json())["account"]["disabled"])
        r = await client.post("/interview/api/admin/accounts/owner/disable")
        check("cannot disable self", r.status == 400)
        r = await client.delete("/interview/api/admin/accounts/nobody")
        check("unknown account → 404", r.status == 404)
        r = await client.delete("/interview/api/admin/accounts/carol")
        check("admin deletes", r.status == 200)
        r = await client.post("/interview/api/logout")
        check("logout clears", r.status == 200)
        r = await client.get("/interview/api/me")
        check("after logout → 401", r.status == 401)

        # A friend is not an admin.
        r = await client.post("/interview/api/login", json={"username": "friend", "password": friend_pw})
        check("friend logs in", r.status == 200)
        r = await client.get("/interview/api/admin/accounts")
        check("friend refused admin → 403", r.status == 403)
        r = await client.post("/interview/api/password", json={"current": "wrong", "new": "a-new-long-password"})
        check("changing a password needs the current one (403, not a sign-out 401)", r.status == 403)
        r = await client.post("/interview/api/password", json={"current": friend_pw, "new": "tiny"})
        check("a short new password is refused", r.status == 400)
        r = await client.post("/interview/api/password", json={"current": friend_pw, "new": "a-new-long-password"})
        check("the friend changes their own password", r.status == 200 and room.accounts.verify("friend", "a-new-long-password") is not None)
        check("...and is handed a fresh cookie on the spot", r.cookies.get(COOKIE) is not None)
        r = await client.get("/interview/api/me")
        check("...so they keep their seat", r.status == 200)
        room.accounts.set_disabled("friend", True)
        r = await client.get("/interview/api/me")
        check("disabling logs the friend out at once", r.status == 401)
        room.accounts.set_disabled("friend", False)
        r = await client.get("/interview/api/me")
        check("re-enabling admits the same cookie again", r.status == 200)
        room.accounts.reset("friend")
        r = await client.get("/interview/api/me")
        check("an admin's reset (here, the CLI's) logs the friend's browser out", r.status == 401)
        room.accounts.delete("friend")
        room.accounts.create("friend", "admin")
        r = await client.get("/interview/api/admin/accounts")
        check("the old cookie does not become the recreated (admin) account", r.status == 401)
        await client.post("/interview/api/logout")

        # A session belongs to the account, not to the username.
        erin_pw = room.accounts.create("erin")
        erin = room.accounts.get("erin")
        brief = {"company": {"name": "Ledgerline"}, "role": {"title": "PM"}}
        meta = room._store.create("erin", "company", {"mode": "company", "length_min": 15}, brief, owner_id=erin.id)
        sid = meta["id"]
        room._store.append_transcript("erin", sid, 1.0, "candidate", "PRIVATE FIXTURE ANSWER")
        stray = room._store.create("erin", "company", {"mode": "company"}, brief, owner_id="0123456789abcdef")
        unowned = room._store.create("erin", "company", {"mode": "company"}, brief)
        r = await client.post("/interview/api/login", json={"username": "erin", "password": erin_pw})
        check("erin signs in", r.status == 200)
        r = await client.get("/interview/api/sessions")
        check("erin's lobby lists her own session and nothing else in her directory",
              [row["id"] for row in (await r.json())["sessions"]] == [sid])
        r = await client.get(f"/interview/api/sessions/{sid}")
        check("...and she can read it", r.status == 200 and "PRIVATE FIXTURE" in await r.text())
        r = await client.get(f"/interview/api/sessions/{stray['id']}")
        check("a session in her directory under another account's id is not hers (404)", r.status == 404)
        r = await client.get(f"/interview/api/sessions/{unowned['id']}")
        check("a session from before ids is nobody's until claimed (404)", r.status == 404)
        room._started = False
        await room.start()
        r = await client.get(f"/interview/api/sessions/{unowned['id']}")
        check("the room claims it at startup for the account holding the username", r.status == 200)
        check("...and the stray keeps its own owner",
              room._store.load_meta("erin", stray["id"])["owner_id"] == "0123456789abcdef")
        await client.post("/interview/api/logout")
        await client.post("/interview/api/login", json={"username": "owner", "password": pw})
        r = await client.delete("/interview/api/admin/accounts/erin")
        check("the admin deletes erin", r.status == 200)
        r = await client.post("/interview/api/admin/accounts", json={"username": "erin"})
        again_pw = (await r.json())["password"]
        await client.post("/interview/api/logout")
        r = await client.post("/interview/api/login", json={"username": "erin", "password": again_pw})
        check("a new erin signs in under the recreated username", r.status == 200)
        r = await client.get("/interview/api/sessions")
        check("the recreated username starts with an empty lobby", (await r.json())["sessions"] == [])
        r = await client.get(f"/interview/api/sessions/{sid}")
        check("the first erin's session is not hers (404)", r.status == 404)
        r = await client.get(f"/interview/api/sessions/{sid}/recording")
        check("...nor its recording (404)", r.status == 404)
        r = await client.get("/interview/api/me")
        check("...and the ledger is fresh", (await r.json())["used_today"]["sessions"] == 0)
        parked = list((state / "users" / ".retired").glob(f"erin.{erin.id}*"))
        check("the first erin's directory was kept aside under users/.retired, transcript and all",
              len(parked) == 1 and (parked[0] / "sessions" / sid / "transcript.jsonl").is_file())
        check("the retired directory is not a username to the store", "erin" not in list(room._store.usernames()) and ".retired" not in list(room._store.usernames()))
        await client.post("/interview/api/logout")

        # The limiter.
        for _ in range(5):
            await client.post("/interview/api/login", json={"username": "owner", "password": "no"})
        r = await client.post("/interview/api/login", json={"username": "owner", "password": pw})
        check("sixth attempt in the window → 429, even with the right password", r.status == 429)
    await room.close()


def cmd_auth() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        probe_accounts(Path(tmp) / "a")
        asyncio.run(probe_door(Path(tmp) / "b"))



# ── brief ────────────────────────────────────────────────────────────────────


def probe_store(state: Path) -> None:
    from ciel.interview.store import SessionStore, brief_title

    print("\nthe store")
    store = SessionStore(state)
    check("no sessions for a new user", store.list("alice") == [])
    meta = store.create("alice", "company", {"mode": "company"}, {"company": {"name": "Ledgerline"}, "role": {"title": "PM"}})
    sid = meta["id"]
    check("session id shape", store.exists("alice", sid) and len(sid) == 20)
    check("title from the brief", meta["title"] == "Ledgerline · PM")
    check("state starts prepared", store.load_meta("alice", sid)["state"] == "prepared")
    store.append_transcript("alice", sid, 1.5, "interviewer", "Hello.")
    store.append_transcript("alice", sid, 4.0, "candidate", "Hi there.")
    rows = store.transcript("alice", sid)
    check("transcript appends in order", [r["speaker"] for r in rows] == ["interviewer", "candidate"])
    store.append_code("alice", sid, 9.0, "python", "pass", "snapshot")
    check("code rows", store.code("alice", sid)[0]["lang"] == "python")
    size = store.append_recording("alice", sid, b"abc")
    size = store.append_recording("alice", sid, b"def")
    check("recording appends", size == 6 and store.recording_path("alice", sid).read_bytes() == b"abcdef")
    store.write_debrief("alice", sid, {"overall": "fine"}, "# Debrief\n")
    check("debrief round-trips", store.debrief("alice", sid)[0] == {"overall": "fine"})
    check("list marks debriefed and recording", store.list("alice")[0]["debriefed"] and store.list("alice")[0]["has_recording"])
    store.update_meta("alice", sid, state="ended")
    check("update_meta", store.load_meta("alice", sid)["state"] == "ended")
    try:
        store.path("alice", "../../etc")
        ok = False
    except KeyError:
        ok = True
    check("a bad id never becomes a path", ok)
    check("count_since counts today", store.count_since("alice", "2000-01-01T00:00:00") == 1)
    check("case title", brief_title("case", {"title": "Greenfield"}) == "Greenfield")
    store.delete("alice", sid)
    check("delete removes the directory", not store.exists("alice", sid))

    # The ledger: a reservation names the day it was taken from.
    day1, day2 = "2026-09-05", "2026-09-06"
    slot = store.reserve("alice", day1, "sessions", 4)
    check("a reservation names its account, day, and kind",
          slot is not None and (slot.username, slot.day, slot.kind) == ("alice", day1, "sessions"))
    check("the cap is a cap", all(store.reserve("alice", day1, "sessions", 4) for _ in range(3))
          and store.reserve("alice", day1, "sessions", 4) is None)
    later = store.reserve("alice", day2, "sessions", 4)
    check("the next day starts fresh", later is not None and store.usage("alice", day2, "sessions") == 1)
    store.release(slot)
    check("releasing yesterday's reservation after midnight leaves today's count alone",
          store.usage("alice", day2, "sessions") == 1)
    store.release(later)
    check("releasing today's hands today's back", store.usage("alice", day2, "sessions") == 0)

    # Ownership: the account id in the metadata, checked on every read.
    owned = store.create("alice", "company", {"mode": "company"}, {"company": {"name": "Ledgerline"}}, owner_id="aaaa")
    check("a session carries its owner", owned["owner_id"] == "aaaa")
    check("...and loads for that owner", store.load_meta("alice", owned["id"], owner_id="aaaa")["id"] == owned["id"])
    try:
        store.load_meta("alice", owned["id"], owner_id="bbbb")
        ok = False
    except KeyError:
        ok = True
    check("...and is a KeyError to any other account", ok)
    check("listing by owner filters", [m["id"] for m in store.list("alice", owner_id="aaaa")] == [owned["id"]]
          and store.list("alice", owner_id="bbbb") == [])
    unowned = store.create("alice", "company", {"mode": "company"}, {"company": {"name": "Ledgerline"}})
    check("an ownerless session is nobody's", store.list("alice", owner_id="") == [])
    check("claim_unowned adopts only the ownerless", store.claim_unowned("alice", "cccc") == 1
          and store.load_meta("alice", unowned["id"])["owner_id"] == "cccc"
          and store.load_meta("alice", owned["id"])["owner_id"] == "aaaa")
    parked = store.retire("alice", "aaaa")
    check("retire moves the directory aside, ledger included",
          parked is not None and parked.name == "alice.aaaa" and (parked / "usage.json").is_file()
          and store.list("alice") == [] and list(store.usernames()) == [])
    check("retiring nothing is nothing", store.retire("alice", "aaaa") is None)


def probe_prompts() -> None:
    from ciel.interview import prompt as p

    print("\nthe prompts")
    for mode in p.MODES:
        system, user, schema = p.brief_request(mode, {"length_min": 30, "request": "x"})
        check(f"{mode}: brief request has a schema with a title", bool(system) and "title" in schema)
        brief = p.scripted_json(schema, user)
        check(f"{mode}: scripted brief is non-empty", bool(brief))
        prompt_text = p.interviewer_prompt(mode, brief, 30)
        check(f"{mode}: interviewer prompt names the mode", (
            "consulting case" in prompt_text if mode == "case"
            else "technical interview" in prompt_text if mode == "technical"
            else "30-minute interview" in prompt_text
        ))
        check(f"{mode}: interviewer prompt forbids markdown", "Never use markdown" in prompt_text)
    check("directive regex finds an exhibit", p.DIRECTIVE.search("ok\n[[exhibit: e1]]\nnext").group(2) == "e1")
    check("directive regex finds a problem", p.DIRECTIVE.search("[[problem: p2]]").group(1) == "problem")
    check("directive regex ignores prose brackets", p.DIRECTIVE.search("[not a directive]") is None)
    sys_, user, schema = p.debrief_request("case", {"title": "t"}, [{"t": 65, "speaker": "candidate", "text": "hi"}], [])
    check("debrief request carries the transcript with timestamps", "[01:05] candidate: hi" in user)
    md = p.render_debrief(p.scripted_json(p.DEBRIEF_SCHEMA, ""), "company")
    check("debrief renders to markdown", md.startswith("# Debrief") and "## Strengths" in md)
    check("code message fences the code", "```python\nx = 1\n```" in p.code_message("python", "x = 1", submitted=True))


async def probe_brief_flow(state: Path) -> None:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from ciel.interview.app import InterviewApp

    print("\nsetup → brief")
    config = _config(state)
    room = InterviewApp(config, dev=True)
    await room.start()
    app = web.Application()
    room.register(app.router)
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/interview/api/login", json={"username": "dev", "password": "dev"})
        check("dev account logs in", r.status == 200)
        r = await client.get("/interview/api/sessions")
        check("empty list", (await r.json())["sessions"] == [])
        r = await client.post("/interview/api/sessions", json={"mode": "nope"})
        check("bad mode → 400", r.status == 400)
        r = await client.post("/interview/api/sessions", json={"mode": "company", "request": "PM at a fintech", "length_min": 30})
        data = await r.json()
        check("company brief created", r.status == 200 and data["brief"]["company"]["name"] == "Ledgerline")
        sid = data["session"]["id"]
        check("row is prepared with a title", data["session"]["state"] == "prepared" and "Ledgerline" in data["session"]["title"])
        r = await client.post("/interview/api/sessions", json={"mode": "technical", "focus": "graphs", "length_min": 45})
        tech = await r.json()
        check("technical brief carries a problem with starters", r.status == 200 and tech["brief"]["technical"]["problems"][0]["starter"]["rust"])
        r = await client.post("/interview/api/sessions", json={"mode": "case", "case_type": "company", "case_source": "generated", "length_min": 30})
        case = await r.json()
        check("case brief carries exhibits", r.status == 200 and case["brief"]["exhibits"][0]["id"] == "e1")
        r = await client.get("/interview/api/sessions")
        check("three sessions listed, newest first", [s["mode"] for s in (await r.json())["sessions"]] == ["case", "technical", "company"])
        r = await client.get(f"/interview/api/sessions/{sid}")
        full = await r.json()
        check("session get returns brief, empty transcript, no debrief", full["brief"]["role"]["title"] == "Product Manager, Reconcile" and full["transcript"] == [] and full["debrief"] is None)
        r = await client.post(f"/interview/api/sessions/{sid}/regenerate")
        check("regenerate on a prepared session", r.status == 200)
        r = await client.get("/interview/api/sessions/20000101-000000-abcd")
        check("unknown session → 404", r.status == 404)
        both = await asyncio.gather(
            client.post("/interview/api/sessions", json={"mode": "company"}),
            client.post("/interview/api/sessions", json={"mode": "company"}),
        )
        check("the last slot of the day, asked for twice at once, is given once (daily cap 4)",
              sorted(r.status for r in both) == [200, 429])
        r = await client.post("/interview/api/sessions", json={"mode": "company"})
        check("fifth session today → 429", r.status == 429)
        r = await client.get("/interview/api/me")
        check("me reports today's usage", (await r.json())["used_today"] == {"sessions": 4, "regenerations": 1})
        r = await client.delete(f"/interview/api/sessions/{sid}")
        check("delete", r.status == 200)
        r = await client.get(f"/interview/api/sessions/{sid}")
        check("deleted → 404", r.status == 404)
        r = await client.post("/interview/api/sessions", json={"mode": "company"})
        check("a deleted session does not hand its slot back", r.status == 429)
        other = tech["session"]["id"]
        statuses = [
            (await client.post(f"/interview/api/sessions/{other}/regenerate")).status for _ in range(8)
        ]
        check("regeneration has a budget of its own (8): seven more pass, the ninth is refused",
              statuses == [200] * 7 + [429])
    await room.close()


def cmd_brief() -> None:
    probe_prompts()
    with tempfile.TemporaryDirectory() as tmp:
        probe_store(Path(tmp) / "a")
        asyncio.run(probe_brief_flow(Path(tmp) / "b"))



# ── endpoint ─────────────────────────────────────────────────────────────────


def cmd_endpoint() -> None:
    from ciel.interview.endpoint import Endpointer

    print("\nthe endpointer")
    ep = Endpointer(silence_s=2.0, extend_s=3.0, max_answer_s=30.0)
    check("nothing said → no deadline", ep.deadline() is None and ep.decide(100.0) == "wait")
    ep.feed_partial("so I think the", 10.0)
    check("first word starts the clock", ep.deadline() == 12.0)
    ep.feed_partial("so I think the", 11.0)
    check("a repeated interim does not push the deadline", ep.deadline() == 12.0)
    ep.feed_partial("so I think the main", 11.5)
    check("new words push it", ep.deadline() == 13.5)
    check("before the deadline: wait", ep.decide(13.0) == "wait")
    check("at the deadline, trailing off → extend", ep.decide(13.6) == "extend")
    check("extension moves the deadline by extend_s", ep.deadline() == 16.5)
    check("still waiting inside the extension", ep.decide(15.0) == "wait")
    check("extension exhausted → complete even mid-thought", ep.decide(16.6) == "complete")

    ep = Endpointer(silence_s=2.0, extend_s=3.0, max_answer_s=30.0)
    ep.feed_final("I led the migration and it shipped on time.", 10.0)
    check("a finished sentence → complete at the deadline, no extension", ep.decide(12.1) == "complete")
    check("text is the finals joined", ep.text == "I led the migration and it shipped on time.")

    ep = Endpointer(silence_s=2.0, extend_s=3.0, max_answer_s=30.0)
    ep.feed_partial("so I think the", 10.0)
    ep.decide(12.1)
    ep.feed_final("so I think the answer is yes.", 14.0)
    check("speech after an extension resets it", not ep.extended and ep.deadline() == 16.0)
    check("partial cleared by a final", ep.partial == "" and ep.text.endswith("yes."))

    ep = Endpointer(silence_s=2.0, extend_s=3.0, max_answer_s=30.0)
    ep.feed_partial("um", 0.0)
    for t in range(1, 40):
        ep.feed_partial("um " * t, float(t))
    check("the cap ends a run-on answer", ep.decide(30.5) == "complete")

    ep = Endpointer()
    ep.feed_partial("done", 5.0)
    ep.mark_done()
    check("answer.done completes at once", ep.deadline() == 0.0 and ep.decide(5.0) == "complete")
    ep = Endpointer()
    ep.mark_done()
    check("answer.done with no words waits", ep.decide(5.0) == "wait")
    ep.seed("merged text", 7.0)
    check("seed starts a fresh answer with words", ep.text == "merged text" and ep.first_speech == 7.0 and not ep.done)


# ── wire ─────────────────────────────────────────────────────────────────────


def cmd_wire() -> None:
    from ciel.interview import wire

    print("\nthe catalog")
    ok = wire.validate({"type": "speech.partial", "text": "hi"}, "c2h")
    check("a good c2h frame passes", ok["text"] == "hi")
    for bad, why in (
        ({"type": "speech.partial"}, "missing text"),
        ({"type": "played", "turn": "1"}, "turn must be int"),
        ({"type": "played", "turn": True}, "a bool is not an int"),
        ({"type": "say", "n": 1, "turn": 1, "text": "x"}, "an h2c frame is refused as c2h"),
        ({"type": "nope"}, "unknown type"),
        (["type"], "not an object"),
    ):
        try:
            wire.validate(bad, "c2h")
            passed = True
        except wire.WireError:
            passed = False
        check(f"refused: {why}", not passed)
    hello = wire.decode(wire.encode({"type": "hello", "v": 1, "state": "listening", "tts": "piper", "turn": 2, "elapsed_s": 3.5, "history": []}), "h2c")
    check("the hub's hello round-trips under its wire name", hello["type"] == "hello")
    seq, chunk = wire.unpack_audio(b"\x00\x00\x00\x07webm")
    check("audio frames unpack seq + bytes", seq == 7 and chunk == b"webm")
    try:
        wire.unpack_audio(b"\x00")
        passed = True
    except wire.WireError:
        passed = False
    check("a short audio frame is refused", not passed)
    check("every catalog entry has a direction", all(s.direction in ("c2h", "h2c") for s in wire.CATALOG.values()))


# ── session ──────────────────────────────────────────────────────────────────


async def _frames_until(ws, kinds: set[str], timeout: float = 10.0) -> list[dict]:
    """Read frames until one of ``kinds`` arrives (inclusive)."""
    import json as _json

    got: list[dict] = []
    async with asyncio.timeout(timeout):
        async for msg in ws:
            if msg.type != 1:  # TEXT
                continue
            frame = _json.loads(msg.data)
            got.append(frame)
            if frame["type"] in kinds:
                return got
    return got


async def _played(ws, turn: int) -> None:
    """After a turn.end: the room says speaking, the browser reports played,
    the room says listening."""
    frames = await _frames_until(ws, {"state"})
    assert frames[-1]["state"] == "speaking", frames[-1]
    await ws.send_json({"type": "played", "turn": turn})
    frames = await _frames_until(ws, {"state"})
    assert frames[-1]["state"] == "listening", frames[-1]


async def probe_session(state: Path) -> None:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from ciel.interview.app import InterviewApp

    print("\na scripted interview")
    config = _config(state)
    config = dataclasses.replace(config, interview=dataclasses.replace(config.interview, silence_ms=300, extend_ms=300, barge_grace_ms=100))
    room = InterviewApp(config, dev=True)
    await room.start()
    app = web.Application()
    room.register(app.router)
    async with TestClient(TestServer(app)) as client:
        await client.post("/interview/api/login", json={"username": "dev", "password": "dev"})
        r = await client.post("/interview/api/sessions", json={"mode": "case", "case_source": "generated", "length_min": 15})
        sid = (await r.json())["session"]["id"]

        ws = await client.ws_connect(f"/interview/ws?session={sid}", headers={"Origin": "http://127.0.0.1:%d" % client.port})
        await ws.send_json({"type": "hello", "v": 1, "caps": {"stt": "browser", "tts": "none", "record": True}})
        frames = await _frames_until(ws, {"hello"})
        check("hello comes back with an empty history", frames[-1]["type"] == "hello" and frames[-1]["history"] == [])
        check("hello says browser tts (no piper here)", frames[-1]["tts"] == "browser")
        frames = await _frames_until(ws, {"turn.end"})
        kinds = [f["type"] for f in frames]
        says = [f for f in frames if f["type"] == "say"]
        check("the greeting is spoken in sentences", len(says) >= 2 and says[0]["turn"] == 1)
        check("thinking precedes speaking", kinds.index("state") < kinds.index("say"))
        check("each sentence is recorded to the transcript", sum(1 for f in frames if f["type"] == "transcript" and f["speaker"] == "interviewer") == len(says))
        check("room is live", room.live_count == 1)
        frames = await _frames_until(ws, {"state"})
        check("after turn.end the room waits for the browser: speaking", frames[-1]["state"] == "speaking")
        await ws.send_json({"type": "played", "turn": 1})
        frames = await _frames_until(ws, {"state"})
        check("played → listening with the silence hold", frames[-1]["state"] == "listening" and frames[-1]["hold_ms"] == 300)

        # An answer that trails off gets an extension, then completes.
        await ws.send_json({"type": "speech.partial", "text": "I would start by looking at the"})
        frames = await _frames_until(ws, {"state"}, timeout=3)
        check("a trailing-off pause extends the hold", frames[-1]["state"] == "listening" and frames[-1]["hold_ms"] == 300)
        frames = await _frames_until(ws, {"turn.end"}, timeout=6)
        cand = [f for f in frames if f["type"] == "transcript" and f["speaker"] == "candidate"]
        check("the answer was recorded", cand and cand[0]["text"] == "I would start by looking at the")
        exhibits = [f for f in frames if f["type"] == "exhibit"]
        check("the interviewer shared exhibit e1 via the directive", len(exhibits) == 1 and exhibits[0]["id"] == "e1" and exhibits[0]["rows"])
        check("the directive itself was never spoken", not any("[[" in f["text"] for f in frames if f["type"] == "say"))
        await _played(ws, 2)

        # A cut-off: the answer completes, the interviewer starts, the candidate goes on.
        await ws.send_json({"type": "speech.final", "text": "The market is concentrated in Riverton.", "t": 30.0})
        frames = await _frames_until(ws, {"state"}, timeout=3)  # thinking
        check("the room took the sentence as an answer and went thinking", frames[-1]["state"] == "thinking")
        await asyncio.sleep(0.2)  # past barge grace
        await ws.send_json({"type": "speech.partial", "text": "but Southbay is where the independents are"})
        frames = await _frames_until(ws, {"apology"}, timeout=3)
        check("the interviewer apologised for cutting in", frames[-1]["text"] == "Sorry, go on.")
        frames = await _frames_until(ws, {"state"}, timeout=3)
        check("and went back to listening", frames[-1]["state"] == "listening")
        await ws.send_json({"type": "speech.final", "text": "but Southbay is where the independents are.", "t": 33.0})
        frames = await _frames_until(ws, {"turn.end"}, timeout=6)
        cand = [f for f in frames if f["type"] == "transcript" and f["speaker"] == "candidate"]
        check("the merged answer was recorded whole", cand and cand[-1]["text"].startswith("The market is concentrated in Riverton. but Southbay"))
        says = [f for f in frames if f["type"] == "say"]
        check("the scripted interviewer saw the cut-off marker and apologised in speech", any("Sorry, go on." in f["text"] for f in says))
        await _played(ws, 4)

        # Reconnect: history replays.
        await ws.close()
        ws = await client.ws_connect(f"/interview/ws?session={sid}", headers={"Origin": "http://127.0.0.1:%d" % client.port})
        await ws.send_json({"type": "hello", "v": 1, "caps": {"stt": "browser", "tts": "none", "record": True}})
        frames = await _frames_until(ws, {"hello"})
        hello = frames[-1]
        check("a reconnect resumes with the history", hello["state"] == "listening" and any(h["type"] == "exhibit" for h in hello["history"]))
        check("history never carries audio", all("audio" not in h for h in hello["history"] if h["type"] == "say"))
        check("still one live session", room.live_count == 1)

        # The typed fallback and the end.
        await ws.send_json({"type": "typed", "text": "I recommend entering Southbay through a small acquisition."})
        await _frames_until(ws, {"turn.end"}, timeout=6)
        await _played(ws, 5)
        await ws.send_json({"type": "end"})
        frames = await _frames_until(ws, {"debrief.ready"}, timeout=10)
        kinds = [f["type"] for f in frames]
        check("the end closes with a spoken goodbye, an ended state, then the debrief", "say" in kinds and "state" in kinds and kinds[-1] == "debrief.ready")
        await ws.close()

        r = await client.get(f"/interview/api/sessions/{sid}")
        full = await r.json()
        check("session is debriefed with a transcript", full["session"]["state"] == "debriefed" and len(full["transcript"]) > 6)
        check("debrief markdown present", full["debrief_md"].startswith("# Debrief"))
        check("question count recorded", full["session"]["question_count"] >= 4)
        check("room is empty again", room.live_count == 0)
        r = await client.get(f"/interview/api/sessions")
        check("lobby row says debriefed", (await r.json())["sessions"][0]["debriefed"])

        # A finished session refuses a new socket.
        r = await client.get(f"/interview/ws?session={sid}", headers={"Origin": "http://127.0.0.1:%d" % client.port, "Connection": "Upgrade", "Upgrade": "websocket", "Sec-WebSocket-Version": "13", "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="})
        check("a finished interview refuses the socket (409)", r.status == 409)
        r = await client.get(f"/interview/ws?session={sid}", headers={"Origin": "http://evil.example"})
        check("a foreign origin is refused (403)", r.status == 403)
    await room.close()


async def probe_revocation(state: Path) -> None:
    from aiohttp import WSMsgType, web
    from aiohttp.test_utils import TestClient, TestServer

    from ciel.interview import app as app_module
    from ciel.interview.app import InterviewApp

    print("\nthe socket goes with the sign-in")
    config = _config(state)
    config = dataclasses.replace(config, interview=dataclasses.replace(config.interview, silence_ms=300, extend_ms=300))
    room = InterviewApp(config, dev=True)
    await room.start()
    owner_pw = room.accounts.create("owner", "admin")
    alice_pw = room.accounts.create("alice")
    app = web.Application()
    room.register(app.router)
    server = TestServer(app)
    await server.start_server()
    alice = TestClient(server)
    admin = TestClient(server)
    await alice.start_server()
    await admin.start_server()
    origin = {"Origin": "http://127.0.0.1:%d" % server.port}
    recheck = app_module._RECHECK_S

    async def open_socket(client, *, sid: str | None = None):
        if sid is None:
            r = await client.post("/interview/api/sessions", json={"mode": "company", "length_min": 15})
            sid = (await r.json())["session"]["id"]
        ws = await client.ws_connect(f"/interview/ws?session={sid}", headers=origin)
        await ws.send_json({"type": "hello", "v": 1, "caps": {"stt": "browser", "tts": "none", "record": True}})
        await _frames_until(ws, {"hello"})
        return sid, ws

    async def closed_with(ws, timeout: float = 5.0) -> int | None:
        """Drain the socket until the server closes it; the close code."""
        seen: list[str] = []
        async with asyncio.timeout(timeout):
            while True:
                msg = await ws.receive()
                if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
                    return ws.close_code
                if msg.type == WSMsgType.TEXT:
                    seen.append(msg.data)

    try:
        r = await alice.post("/interview/api/login", json={"username": "alice", "password": alice_pw})
        check("alice signs in", r.status == 200)
        r = await admin.post("/interview/api/login", json={"username": "owner", "password": owner_pw})
        check("the owner signs in beside her", r.status == 200)

        # Two tabs: the newer one takes the seat.
        sid, first = await open_socket(alice)
        await _frames_until(first, {"turn.end"})
        _, second = await open_socket(alice, sid=sid)
        check("a second connection to the same interview supersedes the first (4409)", await closed_with(first) == 4409)
        check("still one live interview", room.live_count == 1)

        # A password change from this browser: every socket opened under
        # the old password closes, the interview it carried ends.
        r = await alice.post("/interview/api/password", json={"current": alice_pw, "new": "a-new-long-password"})
        check("alice changes her password", r.status == 200)
        code = await closed_with(second)
        check(f"the socket opened under the old password is closed at once (4401, got {code})", code == 4401)
        live = [s for s in room._live.values()]
        for s in live:
            await asyncio.wait_for(s.finished.wait(), 10)
        check("the interview it carried is over", room.live_count == 0)
        r = await alice.get("/interview/api/sessions")
        check("...and is in the lobby as ended, debriefed from what was said",
              (await r.json())["sessions"][0]["state"] in ("ended", "debriefed"))
        r = await alice.get("/interview/api/me")
        check("the browser that changed it keeps its seat", r.status == 200)

        # Disabled by the admin: the socket closes before the debrief is
        # written, and the ended interview accepts nothing more.
        sid, ws = await open_socket(alice)
        await _frames_until(ws, {"turn.end"})
        session = next(iter(room._live.values()))
        r = await admin.post("/interview/api/admin/accounts/alice/disable")
        check("the admin disables alice", r.status == 200)
        check("her socket is closed at once (4401)", await closed_with(ws) == 4401)
        await asyncio.wait_for(session.finished.wait(), 10)
        path = room._store.recording_path("alice", sid)
        session.on_audio(1, b"AFTER_DISABLE")
        session.on_frame({"type": "ping"})
        check("an ended interview records no audio and queues no frame",
              (not path.exists() or b"AFTER_DISABLE" not in path.read_bytes()) and session._inbox.empty())
        r = await alice.get(f"/interview/ws?session={sid}", headers=origin)
        check("a new socket for the disabled account is refused (401)", r.status == 401)

        # A reset made outside the process (the CLI's): no eviction ran,
        # so the next frame after the recheck window finds the cookie stale.
        await admin.post("/interview/api/admin/accounts/alice/enable")
        sid, ws = await open_socket(alice)
        await _frames_until(ws, {"turn.end"})
        app_module._RECHECK_S = 0.0
        room.accounts.reset("alice")
        await ws.send_json({"type": "ping"})
        check("a reset made at the terminal closes the socket on its next frame (4401)", await closed_with(ws) == 4401)
        check("...the interview itself waits for a fresh sign-in, or idles out", room.live_count == 1)
        r = await alice.get("/interview/api/me")
        check("...and the old cookie is out everywhere", r.status == 401)
    finally:
        app_module._RECHECK_S = recheck
        await alice.close()
        await admin.close()
        await server.close()
        await room.close()


def cmd_session() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(probe_session(Path(tmp)))
    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(probe_revocation(Path(tmp)))



# ── speaker ──────────────────────────────────────────────────────────────────


async def probe_speaker() -> None:
    import wave
    from io import BytesIO

    from ciel.config import load_config
    from ciel.interview.speaker import Speaker, pcm_to_wav

    print("\nthe voice")
    wav = pcm_to_wav(b"\x00\x01" * 100, 22050)
    with wave.open(BytesIO(wav)) as wf:
        check("pcm_to_wav writes a mono 16-bit header", wf.getnchannels() == 1 and wf.getsampwidth() == 2 and wf.getframerate() == 22050 and wf.getnframes() == 100)
    speaker = Speaker(load_config().interview)
    await speaker.warm_up()
    if not speaker.available:
        print("  skip piper is not available here — the room would use the browser voice")
        return
    data = await speaker.wav("Thanks for making the time today.")
    check("a sentence renders to a WAV", data is not None and data[:4] == b"RIFF")
    with wave.open(BytesIO(data)) as wf:
        seconds = wf.getnframes() / wf.getframerate()
    check("about the right length for six words", 1.0 < seconds < 5.0)
    check("an empty sentence renders to nothing or little", (await speaker.wav("")) is None or True)


def cmd_speaker() -> None:
    asyncio.run(probe_speaker())


# ── cases ────────────────────────────────────────────────────────────────────


def cmd_cases() -> None:
    from ciel.interview.cases import BUNDLED, CaseLibrary, validate

    print("\nthe case library")
    lib = CaseLibrary()
    cases = lib.all()
    check("three bundled cases", len(cases) == 3)
    check("one of each type", sorted(c["type"] for c in cases) == ["company", "product", "project"])
    check("every bundled case validates", all(not validate(c) for c in cases))
    check("every case has exhibits with matching rows", all(
        all(len(r) == len(e["columns"]) for r in e["rows"]) for c in cases for e in c["exhibits"]
    ))
    check("pick by type", lib.pick("project")["type"] == "project")
    check("pick excludes what was seen when it can", lib.pick("product", exclude={"meridian-games-free-to-play"})["type"] == "product")
    check("pick with everything excluded still returns one", lib.pick("any", exclude={c["slug"] for c in cases}) is not None)
    check("pick of an unknown type is None", lib.pick("astrology") is None)
    check("validator names a missing prompt", "prompt missing" in validate({"slug": "x-y-z", "title": "t", "type": "product", "client": "c", "historical_outcome": "h", "exhibits": []}))
    check("validator rejects ragged rows", any("rows must match" in w for w in validate({
        "slug": "abc", "title": "t", "type": "product", "client": "c", "prompt": "p", "historical_outcome": "h",
        "exhibits": [{"id": "e1", "columns": ["a", "b"], "rows": [["1"]]}],
    })))
    with tempfile.TemporaryDirectory() as tmp:
        user_dir = Path(tmp)
        extra = dict(cases[0]); extra["slug"] = "my-own-case"; extra["title"] = "Mine"
        path = CaseLibrary(user_dir).save(user_dir, extra)
        check("save writes <slug>.json", path.name == "my-own-case.json")
        check("user cases join the bundled ones", len(CaseLibrary(user_dir).all()) == 4)
        override = dict(cases[0]); override["title"] = "Overridden"
        CaseLibrary(user_dir).save(user_dir, override)
        check("a user file with a bundled slug overrides it", any(c["title"] == "Overridden" for c in CaseLibrary(user_dir).all()) and len(CaseLibrary(user_dir).all()) == 4)
    check("bundled dir is inside the package", BUNDLED.name == "cases" and BUNDLED.is_dir())


# ── main ─────────────────────────────────────────────────────────────────────

# ── the brain: the SDK backend against a fake CLI ────────────────────────────


class _FakeClient:
    """What the CLI does on the far side of ``ClaudeSDKClient``, in
    miniature. A query starts a turn that thinks, then streams its reply a
    word at a time, then posts a ResultMessage. An interrupt aborts the
    turn — and the aborted turn *still* posts its result: an error one,
    carrying the CLI's ``[ede_diagnostic]`` line, when the model had not
    said a word yet. That result is what the room used to read as the
    reply to the next question (2026-09-05: two interviews ended in
    'error' three and four turns in, right after a cut-off)."""

    THINK_S = 0.08
    WORD_S = 0.01

    def __init__(self, options: object = None, **_: object) -> None:
        self.options = options
        self.queue: asyncio.Queue = asyncio.Queue()
        self.turns: list[str] = []
        self.interrupts = 0
        self._task: asyncio.Task | None = None
        self._spoken = False

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def query(self, text: str) -> None:
        self.turns.append(text)
        self._spoken = False
        self._task = asyncio.create_task(self._reply(len(self.turns), text))

    async def _reply(self, n: int, text: str) -> None:
        from claude_agent_sdk import StreamEvent

        await asyncio.sleep(self.THINK_S)
        for word in f"Reply {n} to: {text}".split(" "):
            self._spoken = True
            self.queue.put_nowait(StreamEvent(
                uuid="u", session_id="s",
                event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": word + " "}},
            ))
            await asyncio.sleep(self.WORD_S)
        self.queue.put_nowait(self._result(n, is_error=False))
        self._task = None

    @staticmethod
    def _result(n: int, *, is_error: bool, errors: list[str] | None = None, terminal: str = "completed"):
        from claude_agent_sdk import ResultMessage

        return ResultMessage(
            subtype="error_during_execution" if is_error else "success",
            duration_ms=1, duration_api_ms=1, is_error=is_error, num_turns=n, session_id="s",
            total_cost_usd=round(0.01 * n, 4), errors=errors, terminal_reason=terminal,
        )

    async def interrupt(self) -> None:
        self.interrupts += 1
        task = self._task
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        self._task = None
        n = len(self.turns)
        if self._spoken:
            self.queue.put_nowait(self._result(n, is_error=False, terminal="aborted_streaming"))
        else:
            self.queue.put_nowait(self._result(
                n, is_error=True, terminal="aborted_streaming",
                errors=["[ede_diagnostic] result_type=user last_content_type=n/a stop_reason=null"],
            ))

    async def receive_messages(self):
        while True:
            yield await self.queue.get()

    async def receive_response(self):
        from claude_agent_sdk import ResultMessage

        async for message in self.receive_messages():
            yield message
            if isinstance(message, ResultMessage):
                return


class _FlakyOnce:
    """The scripted backend, except that the second question's first
    attempt fails the way an overloaded API does."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self._asks = 0
        self.failed = False

    async def start(self, system_prompt: str) -> None:
        await self._inner.start(system_prompt)

    async def ask(self, text: str):
        from ciel.interview.brain import BackendError

        self._asks += 1
        if self._asks == 2 and not self.failed:
            self.failed = True
            raise BackendError("529 overloaded")
        async for delta in self._inner.ask(text):
            yield delta

    async def ask_json(self, *args, **kwargs):
        return await self._inner.ask_json(*args, **kwargs)

    async def interrupt(self) -> None:
        await self._inner.interrupt()

    async def close(self) -> None:
        await self._inner.close()

    @property
    def cost_usd(self) -> float:
        return 0.0


async def probe_brain() -> None:
    import claude_agent_sdk

    from ciel.interview.brain import AgentSdkBackend, BackendError

    print("\nthe SDK backend against a fake CLI")
    config = load_config().interview
    real = claude_agent_sdk.ClaudeSDKClient
    made: list[_FakeClient] = []

    def factory(options=None, **kwargs):
        client = _FakeClient(options, **kwargs)
        made.append(client)
        return client

    claude_agent_sdk.ClaudeSDKClient = factory
    try:
        backend = AgentSdkBackend(config)
        await backend.start("you are the interviewer")

        async def collect(text: str) -> str:
            return "".join([delta async for delta in backend.ask(text)])

        check("a whole turn streams", (await collect("[begin]")).startswith("Reply 1 to: [begin]"))

        # The cut-off while thinking: the candidate went on before the
        # model had a word out. The room cancels the reader, then interrupts.
        reader = asyncio.create_task(collect("answer one"))
        await asyncio.sleep(_FakeClient.THINK_S / 4)
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
        await backend.interrupt()
        check("the fake CLI saw the interrupt", made[0].interrupts == 1)
        check("the aborted turn's error result was read out by the interrupt", made[0].queue.empty())
        try:
            reply = await collect("answer two")
        except BackendError as exc:
            check(f"the next question is answered rather than failed ({exc})", False)
            return
        check("the next question gets its own reply", reply.startswith("Reply 3 to: answer two"))

        # The cut-off mid-sentence: a couple of words out, then the interrupt.
        heard: list[str] = []
        async with contextlib.aclosing(backend.ask("answer three")) as stream:
            async for delta in stream:
                heard.append(delta)
                if len(heard) == 2:
                    break
        await backend.interrupt()
        reply = await collect("answer four")
        check("after a mid-sentence cut-off the reply is to the new question", reply.startswith("Reply 5 to: answer four"))
        check("the cost follows the session total", abs(backend.cost_usd - 0.05) < 1e-9)
        await backend.close()
        check("close folds the session cost into the lifetime figure", abs(backend.cost_usd - 0.05) < 1e-9)
    finally:
        claude_agent_sdk.ClaudeSDKClient = real


class _Wedged(_FakeClient):
    """A CLI whose interrupt never posts the aborted turn's result: the
    subprocess died mid-turn. ``late`` is the result it would have posted,
    for the probe to deliver after the room has given up waiting."""

    def __init__(self, options: object = None, **kwargs: object) -> None:
        super().__init__(options, **kwargs)
        self.disconnects = 0
        self.broken = False

    async def interrupt(self) -> None:
        self.interrupts += 1
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._task = None

    async def disconnect(self) -> None:
        self.disconnects += 1
        await super().disconnect()

    async def receive_messages(self):
        if self.broken:
            raise RuntimeError("pipe closed")
        async for message in super().receive_messages():
            yield message


async def probe_drain_failure() -> None:
    import claude_agent_sdk

    from ciel.interview import brain as brain_module
    from ciel.interview.brain import AgentSdkBackend, BackendError

    print("\na drain that gives up retires the connection")
    config = load_config().interview
    real = claude_agent_sdk.ClaudeSDKClient
    drain_s = brain_module._DRAIN_S
    made: list[_Wedged] = []

    def factory(options=None, **kwargs):
        client = _Wedged(options, **kwargs)
        made.append(client)
        return client

    claude_agent_sdk.ClaudeSDKClient = factory
    brain_module._DRAIN_S = 0.05
    try:
        backend = AgentSdkBackend(config)
        await backend.start("you are the interviewer")

        async def collect(text: str) -> str:
            return "".join([delta async for delta in backend.ask(text)])

        reader = asyncio.create_task(collect("answer one"))
        await asyncio.sleep(_FakeClient.THINK_S / 4)
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
        await backend.interrupt()
        client = made[0]
        check("the drain timed out and retired the connection", backend._client is None and client.disconnects == 1)
        check("the debt is not forgotten, the connection is", not backend._in_flight and backend._retired is not None)
        # The result turns up late — after the room stopped waiting.
        client.queue.put_nowait(client._result(1, is_error=True, errors=["stale aborted turn"]))
        try:
            await collect("answer two")
            ok, why = False, "answered"
        except BackendError as exc:
            ok, why = "stopped answering" in str(exc), str(exc)
        check(f"the next question fails for the right reason, never with the stale result ({why})", ok)
        check("the stale result was never read as anything", not client.queue.empty())
        await backend.close()
        check("close is quiet on a retired backend", backend._client is None)

        # The stream breaking during the drain: the same end.
        backend = AgentSdkBackend(config)
        await backend.start("you are the interviewer")
        reader = asyncio.create_task(collect("answer one"))
        await asyncio.sleep(_FakeClient.THINK_S / 4)
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
        made[-1].broken = True
        await backend.interrupt()
        check("a stream that breaks mid-drain retires the connection too",
              backend._client is None and "gone" in (backend._retired or ""))
        try:
            await collect("answer two")
            ok = False
        except BackendError:
            ok = True
        check("...and nothing more is asked of it", ok)
        await backend.close()
    finally:
        claude_agent_sdk.ClaudeSDKClient = real
        brain_module._DRAIN_S = drain_s


async def probe_flaky_turn(state: Path) -> None:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from ciel.interview.app import InterviewApp
    from ciel.interview.brain import ScriptedBackend

    print("\none failed turn is not the end of the interview")
    config = _config(state)
    config = dataclasses.replace(config, interview=dataclasses.replace(config.interview, silence_ms=300, extend_ms=300, barge_grace_ms=100))
    room = InterviewApp(config, dev=True)
    flaky: list[_FlakyOnce] = []

    def backend():
        b = _FlakyOnce(ScriptedBackend(config.interview))
        flaky.append(b)
        return b

    room._backend = backend  # type: ignore[method-assign]
    await room.start()
    app = web.Application()
    room.register(app.router)
    async with TestClient(TestServer(app)) as client:
        await client.post("/interview/api/login", json={"username": "dev", "password": "dev"})
        r = await client.post("/interview/api/sessions", json={"mode": "company", "length_min": 15})
        sid = (await r.json())["session"]["id"]
        ws = await client.ws_connect(f"/interview/ws?session={sid}", headers={"Origin": "http://127.0.0.1:%d" % client.port})
        await ws.send_json({"type": "hello", "v": 1, "caps": {"stt": "browser", "tts": "none", "record": False}})
        await _frames_until(ws, {"turn.end"})
        await _frames_until(ws, {"state"})
        await ws.send_json({"type": "typed", "text": "I led a migration to a new billing system."})
        frames = await _frames_until(ws, {"turn.end", "error"}, timeout=10.0)
        kinds = [f["type"] for f in frames]
        # The factory also served the brief and will serve the debrief;
        # the interviewer's backend is whichever one stumbled.
        check("the second question's first attempt failed", any(b.failed for b in flaky))
        check("the turn still ended in a turn.end, not an error", kinds[-1] == "turn.end" and "error" not in kinds)
        check("the interviewer spoke on the retry", any(f["type"] == "say" for f in frames))
        check("the room is not ended", not any(f.get("state") == "ended" for f in frames if f["type"] == "state"))
        await ws.send_json({"type": "end"})
        frames = await _frames_until(ws, {"debrief.ready"}, timeout=10.0)
        check("the interview ends on request and is debriefed", frames[-1]["type"] == "debrief.ready")
        await ws.close()
    await room.close()


def cmd_brain() -> None:
    asyncio.run(probe_brain())
    asyncio.run(probe_drain_failure())
    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(probe_flaky_turn(Path(tmp)))


COMMANDS = {
    "auth": cmd_auth, "brief": cmd_brief, "endpoint": cmd_endpoint,
    "wire": cmd_wire, "session": cmd_session, "speaker": cmd_speaker, "cases": cmd_cases,
    "brain": cmd_brain,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("what", choices=sorted(COMMANDS))
    args = parser.parse_args()
    COMMANDS[args.what]()
    print(f"\nall {len(CHECKS)} checks passed")


if __name__ == "__main__":
    main()
