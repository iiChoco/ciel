"""Probe the tool RPC — the hub's hands on the Mac, over the wire.

    uv run scripts/probe_tool_rpc.py

A loopback pair: the real ``HubServer`` with a planted spoke socket, a
responder task that feeds every ``tool.request`` to a real ``Executor``
whose components are fakes, and the executor's answers routed back as
``tool.result`` frames. Over that, the hub's remote duck types — the
screen, Messages, the locator, the work watcher, the calendar, the Mac's
shell and files — and the tools that bind them, end to end. Then the
edges: no spoke, a spoke that never answers (the deadline, the cancel),
the spoke leaving mid-call, an unknown tool, and the executor's own
guards — the deny tier refused whatever the hub says, the confirm tier
refused without the hub's word, the workspace guard on the files. Quiet
Git writes are refused; cancellation, both deadlines, and shutdown stop
and reap real shell descendants. Mac overwrites keep complete owner-only
snapshots on the hub, restore through ordinary guards, and record honest
notes for missing, oversized, or unreachable originals.
"""

from __future__ import annotations

import asyncio
import base64
import json
import shlex
import sys
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ciel.brain.tools import mac as mac_tools
from ciel.brain.agent import Brain
from ciel.brain.permissions import WorkspaceGuard
from ciel.journal import ActionJournal
from ciel.brain.tools import watch as watch_tool
from ciel.brain.tools.screen import bind_screen, look_at_screen
from ciel.config import Config, FilesConfig, HubConfig, ShellConfig, WebConfig
from ciel.hub.rpc import RemoteBindings, RpcUnavailable
from ciel.hub.server import HubServer
from ciel.messages import Contact, Message, MessagesUnavailable
from ciel.proactive.work import Watch
from ciel.remote.web import Admission
from ciel.spoke.executor import Executor

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


# ── fakes for the executor ───────────────────────────────────────────────────


class FakeMessages:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def find_contacts(self, query):
        if query == "nobody":
            return []
        return [Contact(name="Sam Lee", kind="phone", handle="+15550001")]

    async def recent(self, limit=20, handle=None):
        return [Message(text="on my way", from_me=False, handle="+15550001", chat=None,
                        when=datetime(2026, 9, 2, 12, 30), is_group=False)]

    async def send(self, handle, text):
        if handle == "group":
            raise MessagesUnavailable("group sends are broken")
        self.sent.append((handle, text))


class FakeLocator:
    def current(self, max_age_s):
        from ciel.location import Fix
        return Fix(at=1000.0, source="wifi", network="HomeNet")

    def describe(self, fix):
        return "At home — this Mac's network, just now."


class FakeWatcher:
    def __init__(self):
        self.watches: list[Watch] = []

    def add_file(self, target, label, timeout_minutes):
        w = Watch(id=f"w{len(self.watches)+1}", kind="file", target=target, label=label,
                  created_at=1.0, expires_at=1.0 + timeout_minutes * 60)
        self.watches.append(w)
        return w

    def add_pid(self, pid, label, timeout_minutes):
        w = Watch(id=f"w{len(self.watches)+1}", kind="pid", target=str(pid), label=label,
                  created_at=1.0, expires_at=1.0 + timeout_minutes * 60)
        self.watches.append(w)
        return w

    def active(self):
        return list(self.watches)


class FakeCalendar:
    async def agenda_today(self):
        return ["10:00 Standup", "14:00 Dentist"]


def text_of(result: dict) -> str:
    return "\n".join(c["text"] for c in result["content"] if c["type"] == "text")


# ── the loopback pair ────────────────────────────────────────────────────────


class Pair:
    """A hub server, a seated fake spoke, and a real executor answering it."""

    def __init__(self, config: Config, *, answer: bool = True, executor_kwargs=None):
        self.server = HubServer(WebConfig(), config.hub)
        self.queue, _ = self.server._welcome("spoke", Admission(True, role="spoke", client_id="probe"))
        self.queue.get_nowait()  # the hello
        self.executor = Executor(
            config,
            lambda frame: self.server._on_frame(json.dumps(frame), "spoke") or True,
            **(executor_kwargs or {}),
        )
        self.seen: list[dict] = []
        self.answer = answer
        self.task = asyncio.create_task(self._pump())

    async def _pump(self):
        while True:
            f = json.loads(await self.queue.get())
            self.seen.append(f)
            if f["type"] in ("tool.request", "tool.cancel") and self.answer:
                self.executor.handle(f)

    def leave(self):
        self.server._clients.pop("spoke", None)
        self.server._client_left("spoke")

    async def close(self):
        self.task.cancel()
        await self.executor.close()


def make_config(tmp: Path) -> Config:
    return replace(
        Config(),
        state_dir=tmp,
        hub=replace(HubConfig(), speak_timeout_s=1.0),
        shell=replace(ShellConfig(), enabled=True, command_timeout_s=2.0),
        files=replace(FilesConfig(), enabled=True, workspace=tmp / "ws"),
        journal=replace(Config().journal, dir=tmp / "journal"),
    )


# ── the probes ───────────────────────────────────────────────────────────────


async def probe_senses(config: Config) -> None:
    print("the senses, over the wire")
    pair = Pair(config, executor_kwargs=dict(
        messages=FakeMessages(), locator=FakeLocator(), watcher=FakeWatcher(),
        calendar=FakeCalendar(),
    ))
    remote = RemoteBindings(pair.server, config)

    contacts = await remote.messages.find_contacts("sam")
    check("find_contacts crosses the wire as Contact objects",
          contacts == [Contact(name="Sam Lee", kind="phone", handle="+15550001")])
    msgs = await remote.messages.recent(limit=5)
    check("recent messages come back as Message objects with their time",
          len(msgs) == 1 and msgs[0].text == "on my way" and msgs[0].when == datetime(2026, 9, 2, 12, 30))
    await remote.messages.send("+15550001", "hi")
    check("send reaches the Mac's client", pair.executor._messages.sent == [("+15550001", "hi")])
    try:
        await remote.messages.send("group", "hi")
        check("a refused send raises MessagesUnavailable", False)
    except MessagesUnavailable as exc:
        check("a refused send raises MessagesUnavailable", "group sends" in str(exc))

    check("the locator describes itself on the Mac",
          await remote.locator.describe_now(120) == "At home — this Mac's network, just now.")
    fix = await remote.locator.current_fix(120)
    check("a fix crosses as a Fix", fix is not None and fix.network == "HomeNet")

    w = await remote.watcher.add_file("/tmp/out.mp4", "The export has finished.", 30)
    check("a file watch is armed on the Mac and comes back as a Watch",
          w.id == "w1" and w.kind == "file" and w.expires_at == 1.0 + 1800)
    w2 = await remote.watcher.add_pid(4242, "The build is done.", 10)
    check("a pid watch too", w2.kind == "pid" and w2.target == "4242")
    check("active() answers from the heartbeat, empty until one arrives", remote.watcher.active() == [])
    remote.watcher.observe([{"id": "w1", "kind": "file", "target": "/tmp/out.mp4",
                             "label": "x", "created_at": 1.0, "expires_at": 2.0}, {"bad": 1}])
    check("a heartbeat roster parses, skipping malformed entries",
          [x.id for x in remote.watcher.active()] == ["w1"])

    check("the brief's agenda comes from the Mac's store",
          await remote.calendar.agenda_today() == ["10:00 Standup", "14:00 Dentist"])

    print("\nthe screen")
    bind_screen(config.screen, remote.screen)

    async def fake_capture(max_edge):
        return [b"\xff\xd8jpeg-bytes"]

    import ciel.brain.tools.screen as screen_mod
    original = screen_mod.capture_displays
    screen_mod.capture_displays = fake_capture
    try:
        result = await look_at_screen.handler({})
        images = [c for c in result["content"] if c["type"] == "image"]
        check("look_at_screen captures on the Mac and returns the image",
              len(images) == 1 and base64.b64decode(images[0]["data"]) == b"\xff\xd8jpeg-bytes")
    finally:
        screen_mod.capture_displays = original
        bind_screen(config.screen, None)

    print("\nthe watch tool, bound to the wire")
    watch_tool.bind_watcher(remote.watcher)
    result = await watch_tool.watch_for_completion.handler(
        {"path": str(Path(tempfile.gettempdir())), "label": "The thing is done.", "timeout_minutes": 5}
    )
    check("the tool arms a watch on the Mac even for a path that exists here",
          "Watching for" in text_of(result) and "id w3" in text_of(result))
    await pair.close()


async def probe_mac(config: Config) -> None:
    print("\nthe Mac's shell and files")
    (config.files.workspace).mkdir(parents=True, exist_ok=True)
    (config.files.workspace / "notes.txt").write_text("hello from the mac\n")
    pair = Pair(config)
    remote = RemoteBindings(pair.server, config)
    mac_tools.bind_mac(remote.mac, config.shell)

    out = await mac_tools.run_on_mac.handler({"command": "echo hi"})
    check("a quiet command runs and reports exit and stdout",
          "exit 0" in text_of(out) and "hi" in text_of(out))
    seen = [f for f in pair.seen if f["type"] == "tool.request" and f["tool"] == "shell.run"]
    check("the quiet tier rides down unconfirmed", seen[-1]["args"]["confirmed"] is False)

    out = await mac_tools.run_on_mac.handler({"command": "sudo rm -rf /"})
    check("the deny tier is refused on the Mac regardless",
          "refused on the Mac" in text_of(out))

    out = await mac_tools.run_on_mac.handler({"command": "touch newfile"})
    check("a side-effect command from the tool arrives marked confirmed (the hook asked)",
          [f for f in pair.seen if f["type"] == "tool.request"][-1]["args"]["confirmed"] is True
          and "exit 0" in text_of(out))

    res = await pair.server.rpc("shell.run", {"command": "touch other", "confirmed": False}, 5)
    check("without the hub's word, a side-effect command is refused by the spoke",
          res["ok"] is False and "needs the user's yes" in res["error"])

    res = await pair.server.rpc("shell.run", {"command": "sleep 5", "confirmed": True}, 5)
    check("a command past the Mac's timeout is killed and reported",
          res["ok"] is False and "ran past" in res["error"])

    out = await mac_tools.mac_read_file.handler({"path": "notes.txt"})
    check("a relative read resolves inside the Mac's workspace",
          text_of(out) == "hello from the mac\n")
    out = await mac_tools.mac_write_file.handler({"path": "sub/new.txt", "content": "written"})
    check("a write lands, directories made", (config.files.workspace / "sub/new.txt").read_text() == "written")
    out = await mac_tools.mac_list_dir.handler({"path": "."})
    names = set(text_of(out).split())
    check("a listing marks directories (and shows the file the confirmed command made)",
          {"notes.txt", "sub/", "newfile"} <= names and "sub" not in names)
    out = await mac_tools.mac_write_file.handler({"path": "/etc/hosts", "content": "x"})
    check("a write outside the workspace is refused on the Mac",
          "refused on the Mac" in text_of(out))
    out = await mac_tools.mac_read_file.handler({"path": "~/.ssh/id_rsa"})
    check("a forbidden name is refused on the Mac", "refused on the Mac" in text_of(out))
    left, right = config.files.workspace / "left", config.files.workspace / "right"
    left.write_text("before")
    right.write_text("after")
    output = config.state_dir / "outside.patch"
    command = f"git diff --no-index --output={shlex.quote(str(output))} {shlex.quote(str(left))} {shlex.quote(str(right))}"
    res = await pair.server.rpc("shell.run", {"command": command, "confirmed": False}, 5)
    check("a Git output option cannot write outside the workspace without a yes", not res["ok"] and not output.exists())
    cookie = config.files.workspace / "sections-cookie"
    cookie.write_text("SYNTHETIC")
    res = await pair.server.rpc("files.read", {"path": str(cookie)}, 5)
    check("the spoke refuses the signup credential too", not res["ok"] and "off limits" in res["error"])
    await pair.close()


async def probe_edges(config: Config) -> None:
    print("\nthe edges")
    server = HubServer(WebConfig(), config.hub)
    remote = RemoteBindings(server, config)
    try:
        await remote.locator.current_fix(1)
        check("no spoke: RpcUnavailable", False)
    except RpcUnavailable as exc:
        check("no spoke: RpcUnavailable", "not connected" in str(exc))
    check("the location tool's wording survives it",
          "not available" in await remote.locator.describe_now(1))
    mac_tools.bind_mac(remote.mac, config.shell)
    check("the Mac tools say so in words",
          "Could not run that on the Mac" in text_of(await mac_tools.run_on_mac.handler({"command": "ls"})))

    pair = Pair(config, answer=False)
    remote = RemoteBindings(pair.server, config)
    try:
        await pair.server.rpc("screen.capture", {"max_edge": 100}, 0.2)
        check("a spoke that never answers hits the deadline", False)
    except RpcUnavailable as exc:
        check("a spoke that never answers hits the deadline", "did not answer" in str(exc))
    await asyncio.sleep(0.01)
    check("and is sent a cancel", any(f["type"] == "tool.cancel" for f in pair.seen))
    await pair.close()

    pair = Pair(config, answer=False)
    call = asyncio.create_task(pair.server.rpc("screen.capture", {"max_edge": 100}, 5))
    await asyncio.sleep(0.02)
    pair.leave()
    try:
        await call
        check("the spoke leaving mid-call fails the call at once", False)
    except RpcUnavailable as exc:
        check("the spoke leaving mid-call fails the call at once", "disconnected" in str(exc))
    await pair.close()

    pair = Pair(config)
    res = await pair.server.rpc("warp.drive", {}, 2)
    check("an unknown tool is a sentence, not a hang", res["ok"] is False and "does not know" in res["error"])
    res = await pair.server.rpc("messages.recent", {}, 2)
    check("a sense the Mac has off says so", res["ok"] is False and "off on the Mac" in res["error"])
    await pair.close()


async def probe_snapshots(config: Config) -> None:
    pair = Pair(config)
    remote = RemoteBindings(pair.server, config)
    journal = ActionJournal(config.journal)
    brain = Brain(config, journal=journal, mac_tools=True, mac_snapshot=remote.mac.snapshot_file)
    recorder = brain._recorder
    assert recorder is not None
    mac_tools.bind_mac(remote.mac, config.shell)
    target = config.files.workspace / "restore.txt"
    original = "previous contents\n" * 1500
    target.write_text(original)
    args = {"path": "restore.txt", "content": "replacement"}
    payload = {"tool_name": "mcp__ciel__mac_write_file", "tool_input": args}
    await recorder.before(payload, "overwrite", None)
    result = await mac_tools.mac_write_file.handler(args)
    await recorder.after({**payload, "tool_response": result}, "overwrite", None)
    entry = journal.recent(1)[0]
    saved = Path(entry["snapshot"])
    check("a Mac write saves its real previous contents on the hub before overwriting", saved.read_text() == original
          and target.read_text() == "replacement" and entry["note"] is None)
    check("the complete snapshot is owner-only and outside the file workspace", saved.stat().st_mode & 0o777 == 0o600
          and not saved.is_relative_to(config.files.workspace))
    check("the ordinary read guard can read the snapshot for undo", WorkspaceGuard.from_config(config).permits(str(saved)) is None)
    check("a snapshot is never offered as a write destination", WorkspaceGuard.from_config(config).permits(str(saved), write=True) is not None)
    undo = {**payload, "tool_input": {**args, "content": saved.read_text()}}
    await recorder.before(undo, "undo", None)
    result = await mac_tools.mac_write_file.handler(undo["tool_input"])
    await recorder.after({**undo, "tool_response": result}, "undo", None)
    check("ordinary guarded Mac tools can restore every character", target.read_text() == original)
    check("the undo itself has a snapshot too", Path(journal.recent(1)[0]["snapshot"]).read_text() == "replacement")
    for name, contents, wanted in (("missing.txt", None, "did not exist"), ("large.txt", "x" * (config.journal.max_snapshot_kb * 1024 + 1), "too large"), ("empty.txt", "", None)):
        if contents is not None:
            (config.files.workspace / name).write_text(contents)
        item = {**payload, "tool_input": {"path": name, "content": "new"}}
        await recorder.before(item, name, None)
        result = await mac_tools.mac_write_file.handler(item["tool_input"])
        await recorder.after({**item, "tool_response": result}, name, None)
        entry = journal.recent(1)[0]
        check(f"the Mac's {name} has an honest undo record", (entry["snapshot"] is None and wanted in entry["note"])
              if wanted else Path(entry["snapshot"]).read_bytes() == b"")
    for path in ("sections-cookie", str(config.state_dir / "outside.patch")):
        result = await pair.server.rpc("files.snapshot", {"path": path, "max_bytes": 1000}, 5)
        check("the snapshot reader refuses a secret or a path outside the write boundary", not result["ok"])
    pair.leave()
    await recorder.before(payload, "offline", None)
    check("an unreachable Mac records why no snapshot was kept", recorder._pending["offline"][0] is None
          and "not connected" in recorder._pending["offline"][1])
    await pair.close()


async def probe_process_cleanup(config: Config) -> None:
    immediate = Executor(config, lambda frame: True)
    immediate.handle({"type": "tool.request", "rpc_id": "early", "tool": "shell.run", "args": {"command": "echo never"}})
    immediate.handle({"type": "tool.cancel", "rpc_id": "early"})
    await immediate.close()
    check("a call canceled before it starts leaves no unfinished ledger entry", not immediate._tasks)
    for cause in ("cancel", "rpc deadline", "shell deadline", "close", "hub cancel", "startup cancel"):
        marker = config.state_dir / (cause.replace(" ", "-") + "-late")
        ready = marker.with_suffix(".ready")
        worker = marker.with_suffix(".py")
        child = f"import time; from pathlib import Path; time.sleep(0.5); Path({str(marker)!r}).write_text('late')"
        worker.write_text("import subprocess, sys\nfrom pathlib import Path\n"
                          + f"child = subprocess.Popen([sys.executable, '-c', {child!r}])\n"
                          + f"Path({str(ready)!r}).write_text(str(child.pid))\nchild.wait()\n")
        cfg = replace(config, shell=replace(config.shell, command_timeout_s=0.2 if cause == "shell deadline" else 3.0))
        pair = Pair(cfg)
        processes: list[asyncio.subprocess.Process] = []
        spawn = asyncio.create_subprocess_shell

        async def track(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
            proc = await spawn(*args, **kwargs)
            processes.append(proc)
            if cause == "startup cancel":
                await asyncio.sleep(0.15)
            return proc

        with patch("asyncio.create_subprocess_shell", track):
            task = asyncio.create_task(pair.server.rpc("shell.run", {"command": shlex.quote(sys.executable) + " " + shlex.quote(str(worker)), "confirmed": True}, 0.2 if cause == "rpc deadline" else 3.0))
            for _ in range(100):
                if ready.exists():
                    break
                await asyncio.sleep(0.005)
            assert ready.exists(), cause
            if cause == "cancel":
                rpc_id = next(iter(pair.executor._tasks))
                pair.executor.handle({"type": "tool.cancel", "rpc_id": rpc_id})
                cleanup = pair.executor._tasks.get(rpc_id)
                if cleanup is not None:
                    await cleanup
                task.cancel()
            elif cause == "close":
                await pair.executor.close()
                task.cancel()
            elif cause in {"hub cancel", "startup cancel"}:
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # A returned RPC cancellation has queued the frame; let its
            # receiver finish cleanup before checking the OS process.
            for _ in range(100):
                if processes and processes[0].returncode is not None:
                    break
                await asyncio.sleep(0.005)
            await asyncio.sleep(0.6)
            check(f"{cause} stops the shell and its delayed child", not marker.exists())
            check(f"{cause} reaps the shell and finishes its task", bool(processes)
                  and processes[0].returncode is not None and not pair.executor._tasks)
        await pair.close()


async def probe_documents(config: Config) -> None:
    print("\nthe Mac's bound documents")
    from ciel.brain.tools import projects as project_tools
    from ciel.project_work import RemoteWorkbench, WorkLimits
    from ciel.projects import ProjectStore

    home = Path(tempfile.mkdtemp(prefix="ciel-rpc-home-")) / "home"
    course = home / "Berkeley" / "H104" / "hw03"
    course.mkdir(parents=True)
    (course / "hw03.tex").write_text("\\begin{numedquestion} One. \\begin{framed} Done. \\end{framed} \\end{numedquestion}\n\\input{parts/two}\n")
    (course / "parts").mkdir()
    (course / "parts" / "two.tex").write_text("\\begin{numedquestion} Two. \\end{numedquestion}\n")
    (home / "elsewhere.tex").write_text("\\begin{numedquestion} Elsewhere. \\end{numedquestion}\n")
    (course / "notes.pdf").write_bytes(b"%PDF-1.4")
    (course / "id_rsa").write_text("secret")
    opened: list[list[str]] = []

    class Ran:
        returncode = 0
        stderr = ""

    def runner(args, **kwargs):
        opened.append(list(args))
        return Ran()

    with patch.object(Path, "home", return_value=home):
        pair = Pair(config)
        pair.executor._open_runner = runner
        remote = RemoteBindings(pair.server, config)
        data, note = await remote.mac.read_document(str(course / "hw03.tex"), 100000)
        check("a document under home is read as bytes through the spoke", data is not None and b"numedquestion" in data and note is None)
        (home / ".ciel").mkdir()
        (home / ".ciel" / "config.toml").write_text("[x]\n")
        with patch.object(pair.executor, "_config", replace(config, state_dir=home / ".ciel")):
            for path, why in ((str(home / ".ciel" / "config.toml"), "state directory"), (str(course / "id_rsa"), "off limits"),
                              (str(course / "notes.pdf"), "not a document type"), ("relative.tex", "absolute"), ("/etc/hosts", "home folder")):
                try:
                    await remote.mac.read_document(path, 100000)
                    check(f"the spoke refuses {why}", False)
                except Exception as exc:  # noqa: BLE001 - the refusal is the sentence
                    check(f"the spoke refuses a document {why}", "refused on the Mac" in str(exc) and why.split()[0] in str(exc))
        data, note = await remote.mac.read_document(str(course / "hw03.tex"), 10)
        check("a document past the bound is not read, in words", data is None and "larger than 10" in note)
        data, note = await remote.mac.read_document(str(course / "missing.tex"), 1000)
        check("a missing document is said, not raised", data is None and "does not exist" in note)

        said = await remote.mac.open_document(str(course / "hw03.tex"), "TeXShop")
        check("open goes through the Mac's open with the app named", said == "opened hw03.tex with TeXShop" and opened[-1][:3] == ["/usr/bin/open", "-a", "TeXShop"])
        said = await remote.mac.open_document("https://example.test/hw03.pdf", "")
        check("a URL opens the page in the browser", said == "opened the page" and opened[-1] == ["/usr/bin/open", "https://example.test/hw03.pdf"])
        said = await remote.mac.open_document(str(course / "nope.tex"), "")
        check("a path that is not there is not opened, in words", said.startswith("not opened") and len(opened) == 2)
        said = await remote.mac.open_document(str(config.state_dir / "config.toml"), "")
        check("nothing under the state directory is opened", said.startswith("refused") and len(opened) == 2)
        try:
            await remote.mac.open_document(str(course / "hw03.tex"), "Tex Shop; rm")
            check("an opener with a space or a shell character is refused", False)
        except Exception as exc:  # noqa: BLE001
            check("an opener with a space or a shell character is refused", "one app name" in str(exc))

        store = ProjectStore(config.state_dir / "projects")
        store.write("analysis", "hw03 open", description="H104")
        store.bind("analysis", "folder", str(course.parent))
        store.bind("analysis", "solution", str(course / "hw03.tex"), key="hw03", current=True, opener="TeXShop")
        store.bind("analysis", "solution", str(home / "elsewhere.tex"), key="away")
        project_tools.bind_projects(store)
        project_tools.bind_workbench(RemoteWorkbench(remote.mac), WorkLimits(max_bytes=100000, max_includes=4, include_depth=2))
        out = text_of(await project_tools.project_progress.handler({"project": "analysis"}))
        check("project_progress reads the current solution through the Mac, follows the include within the bound folder, and reports both questions",
              "2 questions: 1 written, 0 in progress, 1 not started" in out and "- 2 [question] not started" in out and "two.tex" in out
              and "Revisions read: hw03.tex" in out and "quoted data" in out)
        out = text_of(await project_tools.project_progress.handler({"project": "analysis", "key": "away"}))
        check("a bound document outside the project's folders is not read: the roots are the registered folder",
              "outside the folders bound" in out)
        out = text_of(await project_tools.open_document.handler({"project": "analysis", "role": "solution"}))
        check("open_document opens the current solution with its opener", out.endswith("opened hw03.tex with TeXShop") and opened[-1][2] == "TeXShop")
        pair.leave()
        out = text_of(await project_tools.project_progress.handler({"project": "analysis"}))
        check("with the Mac gone, the reading says so instead of reading the server", "could not be reached" in out or "not connected" in out)
        project_tools.bind_workbench(None)
        await pair.close()

        print("\nthe Mac is told what to watch")
        from ciel.proactive.resources import ResourceWatcher

        class Sink:
            def __init__(self):
                self.n = 0
            def next_id(self):
                self.n += 1
                return str(self.n)
            def push(self, event):
                return True

        watcher = ResourceWatcher(config.state_dir / "spoke-resources.json", Sink(), poll_s=60.0)
        pair = Pair(config, executor_kwargs={"resources": watcher})
        remote = RemoteBindings(pair.server, config)
        result = await remote.mac.watch_resources([str(course / "hw03.tex"), str(course / "notes.pdf"), "/etc/hosts", str(course / "parts" / "two.tex")])
        check("the watched set replaces the last, each path judged by the Mac's own document rules, the refused ones named",
              result["watching"] == 2 and set(result["refused"]) == {str(course / "notes.pdf"), "/etc/hosts"}
              and watcher.watched() == sorted([str((course / "hw03.tex").resolve()), str((course / "parts" / "two.tex").resolve())]))
        await pair.close()
        bare = Pair(config)
        try:
            await RemoteBindings(bare.server, config).mac.watch_resources([str(course / "hw03.tex")])
            check("a Mac with projects off says it is not watching", False)
        except Exception as exc:  # noqa: BLE001
            check("a Mac with projects off says it is not watching", "not watching" in str(exc))
        await bare.close()


async def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    config = make_config(tmp)
    await probe_senses(config)
    await probe_mac(config)
    await probe_documents(config)
    await probe_edges(config)
    await probe_snapshots(config)
    await probe_process_cleanup(config)
    print(f"\nall {len(CHECKS)} checks passed")


if __name__ == "__main__":
    asyncio.run(main())
