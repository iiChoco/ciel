"""Projects — durable named workspaces for work that spans conversations.

Codename: **Atlas** — each project file a chart of some territory of ongoing
work, the index the map that says which chart to open.

Sessions are deliberately short-lived (see ``brain.resume_window_minutes``),
so anything that outlives a conversation needs a durable home. Memory holds
*facts*; this holds *working state*: where an effort stands, the numbers and
paths that matter, what's next. The model opens a project before working on
it and updates it when reality moves, which is what makes "let's get back to
the wake word project" work three days and thirty sessions later.

One Markdown file per project, same reasoning as memory: inspectable,
editable, greppable. Two sections with different physics — ``## State`` is
the complete current picture, *replaced* on every update so it can't
accumulate rot; ``## Log`` is append-only dated history, cheap to grow,
returned only as a tail so it can't flood a context. Timestamps are stored
machine-readable (UTC ISO-8601) and prettified only at render time; a stored
"2 hours ago" is a lie by dinnertime.

Identity comes from the *name*, not the description: the user says "the wake
word project" and the model must land on the same file every time. The index
in the system prompt shows the exact names to use.

**A project is bound to the work it is about.** Since 2026-09-09 a project
also carries the owner's statements about where its work lives, as
frontmatter lines beside the rest: an ``id`` minted the first time the
file is written or bound and kept across a rename, so a later reading in
the task store can name the project by something that does not change;
``aliases``, the other names the owner uses ("analysis homework", "h104"),
which :meth:`ProjectStore.resolve` honours before any guess; and
``resource.<key>`` lines, each a role (solution, handout, folder, draft,
dataset), a source (``local`` or ``url``), whether it is the current one of
its role, an optional opener, and the locator. These are the owner's words,
kept in the owner's file, inspectable and editable like the prose; nothing
here is observed or inferred, and a file from before this date loads
exactly as it did, its prose untouched, until it is bound.
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from ciel.memory.store import atomic_write, slugify

log = logging.getLogger(__name__)

VALID_STATUSES = ("active", "paused", "done")

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)

# The exact strings _save() emits around the state. Parsing leans on the
# writer's structure, not on pattern-matching the content: the state is
# whatever sits between the head prefix and the LAST log delimiter, so a
# state that itself contains "## State" or "## Log" headings round-trips
# byte-exact — the real log delimiter is always the one we appended after it.
_STATE_HEAD = "## State\n\n"
_LOG_DELIM = "\n\n## Log\n\n"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ago(ts: float) -> str:
    """Render a timestamp relative, for the index only — never stored."""
    delta = max(0.0, time.time() - ts)
    if delta < 90 * 60:
        return f"{max(1, int(delta // 60))} minutes ago"
    if delta < 36 * 3600:
        return f"{int(delta // 3600)} hours ago"
    return f"{int(delta // 86400)} days ago"


RESOURCE_SOURCES = ("local", "url")
_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


@dataclass(frozen=True, slots=True)
class Resource:
    """One place a project's work lives, as the owner stated it."""

    key: str
    role: str
    source: str  # "local" | "url"
    locator: str
    current: bool = False
    opener: str = ""
    """An app to open it with; empty means the Mac's default handler."""


@dataclass(frozen=True, slots=True)
class Project:
    name: str
    description: str
    status: str
    state: str
    log: tuple[str, ...]  # newest last; possibly trimmed to a tail
    created_at: float
    updated_at: float
    path: Path
    id: str = ""
    """Stable across renames; empty for a file written before ids existed."""
    aliases: tuple[str, ...] = ()
    resources: tuple[Resource, ...] = ()

    def resource(self, role: str | None = None, key: str | None = None) -> Resource | None:
        """The resource meant: by key, else the current one of the role, else
        the role's only one. None when nothing is that certain."""
        if key:
            return next((r for r in self.resources if r.key == key), None)
        of_role = [r for r in self.resources if role is None or r.role == role]
        current = [r for r in of_role if r.current]
        if len(current) == 1:
            return current[0]
        return of_role[0] if len(of_role) == 1 else None


@dataclass(frozen=True, slots=True)
class Resolution:
    """What a name landed on: one project, or the candidates it could mean."""

    project: Project | None
    candidates: tuple[str, ...] = ()
    """Names, when the text matched more than one; empty when none or one."""


class ProjectStore:
    """File-backed projects: replace-state, append-log."""

    def __init__(
        self,
        directory: Path,
        max_index_entries: int = 30,
        max_state_chars: int = 4000,
        log_tail: int = 15,
        max_resources: int = 24,
    ) -> None:
        self._dir = directory.expanduser()
        self._max_index = max_index_entries
        self._max_state = max_state_chars
        self._log_tail = log_tail
        self._max_resources = max_resources

    @property
    def directory(self) -> Path:
        return self._dir

    # ── reading ──────────────────────────────────────────────────────────────

    def all(self) -> list[Project]:
        """Every project, most recently updated first. Full logs."""
        if not self._dir.exists():
            return []
        found = []
        for path in self._dir.glob("*.md"):
            parsed = self._read(path)
            if parsed is not None:
                found.append(parsed)
        return sorted(found, key=lambda p: p.updated_at, reverse=True)

    def get(self, name: str) -> Project | None:
        """One project by name, its log trimmed to the configured tail."""
        path = self._dir / f"{slugify(name)}.md"
        project = self._read(path) if path.exists() else None
        if project is None:
            return None
        return replace(project, log=project.log[-self._log_tail:])

    def resolve(self, text: str) -> Resolution:
        """The project a phrase means: the exact name first, then an exact
        alias, then a name, alias, or description the phrase is part of or
        that is part of the phrase. One match is an answer; several are a
        question for the owner, never a guess; the description alone counts
        only when nothing else did."""
        wanted = slugify(text)
        if not wanted or wanted == "memory" and not text.strip():
            return Resolution(None)
        exact = self.get(wanted)
        if exact is not None:
            return Resolution(exact)
        projects = self.all()
        by_alias = [p for p in projects if wanted in {slugify(a) for a in p.aliases}]
        if len(by_alias) == 1:
            return Resolution(self.get(by_alias[0].name))
        if by_alias:
            return Resolution(None, tuple(p.name for p in by_alias))
        def contains(p: Project) -> bool:
            names = [p.name, *(slugify(a) for a in p.aliases)]
            return any(n and (n in wanted or wanted in n) for n in names)
        partial = [p for p in projects if contains(p)]
        if not partial:
            partial = [p for p in projects if wanted in slugify(p.description)]
        if len(partial) == 1:
            return Resolution(self.get(partial[0].name))
        return Resolution(None, tuple(p.name for p in partial))

    # ── writing ──────────────────────────────────────────────────────────────

    def write(
        self,
        name: str,
        state: str,
        description: str | None = None,
        status: str | None = None,
    ) -> Project:
        """Create a project or replace its state.

        Raises ``ValueError`` past the state budget rather than truncating:
        the refusal reaches the model as a tool error and teaches it to
        condense; a silent truncation loses whatever was written last and
        teaches nothing.
        """
        state = state.strip()
        if len(state) > self._max_state:
            raise ValueError(
                f"State is {len(state)} characters; the limit is "
                f"{self._max_state}. Condense it — state should be the "
                "current picture, not the history (the log holds history)."
            )

        slug = slugify(name)
        path = self._dir / f"{slug}.md"
        existing = self._read(path) if path.exists() else None
        if existing is None and not description:
            description = name.strip()
        if status is not None and status not in VALID_STATUSES:
            # Raise, don't coerce: silently rewriting a typo'd status to
            # "active" reverts a project the user had marked done or paused,
            # losing exactly the state this store exists to keep. The error
            # reaches the model as a tool failure and teaches the valid set.
            raise ValueError(
                f"Unknown status {status!r}. Use one of: {', '.join(VALID_STATUSES)}."
            )

        now = time.time()
        project = Project(
            name=slug,
            description=(description or (existing.description if existing else name)).strip(),
            status=status or (existing.status if existing else "active"),
            state=state,
            log=existing.log if existing else (),
            created_at=existing.created_at if existing else now,
            updated_at=now,
            path=path,
            id=(existing.id if existing else "") or _mint(),
            aliases=existing.aliases if existing else (),
            resources=existing.resources if existing else (),
        )
        self._save(project)
        log.info("%s project: %s", "updated" if existing else "created", slug)
        return project

    # ── bindings: the owner's statements about where the work lives ──────────

    def _bound(self, name: str) -> Project:
        path = self._dir / f"{slugify(name)}.md"
        existing = self._read(path) if path.exists() else None
        if existing is None:
            raise ValueError(f"No project named '{name}'.")
        return existing if existing.id else replace(existing, id=_mint())

    def set_aliases(self, name: str, aliases: tuple[str, ...] | list[str]) -> Project:
        """The other names the owner uses for the project; replaces the set.
        One line each in the file, so a newline in one is collapsed."""
        cleaned = tuple(dict.fromkeys(" ".join(a.split()) for a in aliases if a and a.strip()))
        if any("," in a for a in cleaned):
            raise ValueError("An alias holds no comma; the list is comma-separated.")
        project = replace(self._bound(name), aliases=cleaned, updated_at=time.time())
        self._save(project)
        return project

    def rename(self, name: str, new_name: str) -> Project:
        """Move the file to the new name, keep the id, and keep the old name
        as an alias so what the owner used to say still lands."""
        existing = self._bound(name)
        new_slug = slugify(new_name)
        if not new_slug or new_slug == "memory":
            raise ValueError("A project name needs a letter or a digit in it.")
        if new_slug == existing.name:
            return existing
        new_path = self._dir / f"{new_slug}.md"
        if new_path.exists():
            raise ValueError(f"A project named '{new_slug}' already exists.")
        aliases = tuple(dict.fromkeys((*existing.aliases, existing.name)))
        project = replace(existing, name=new_slug, path=new_path, aliases=aliases, updated_at=time.time())
        self._save(project)
        try:
            existing.path.unlink()
        except OSError:
            log.warning("renamed project %s but could not remove %s", new_slug, existing.path)
        log.info("renamed project %s to %s", existing.name, new_slug)
        return project

    def bind(self, name: str, role: str, locator: str, *, source: str = "", key: str = "",
             current: bool = False, opener: str = "") -> Resource:
        """Connect one place to a project. A key names it (the role's slug
        by default); binding a known key replaces that resource. Marking it
        current unmarks the role's other resources: one current per role."""
        project = self._bound(name)
        role = slugify(role)
        if not role or role == "memory":
            raise ValueError("A resource needs a role: solution, handout, folder, draft, dataset, reference…")
        locator = " ".join(locator.split())
        if not locator:
            raise ValueError("A resource needs a locator: a path on the Mac or a URL.")
        source = source or ("url" if locator.lower().startswith(("http://", "https://")) else "local")
        if source not in RESOURCE_SOURCES:
            raise ValueError(f"The source is one of: {', '.join(RESOURCE_SOURCES)}.")
        key = slugify(key) if key else role
        if not _KEY.match(key):
            raise ValueError("A resource key is a short slug: letters, digits, dashes.")
        opener = " ".join(opener.split())
        if any(ch.isspace() for ch in opener):
            raise ValueError("An opener is one word: an app's name with no spaces.")
        others = tuple(r for r in project.resources if r.key != key)
        if len(others) >= self._max_resources:
            raise ValueError(f"A project holds at most {self._max_resources} resources; unbind one first.")
        if current:
            others = tuple(replace(r, current=False) if r.role == role else r for r in others)
        resource = Resource(key, role, source, locator, current, opener)
        self._save(replace(project, resources=(*others, resource), updated_at=time.time()))
        return resource

    def unbind(self, name: str, key: str) -> bool:
        """Forget one resource by key. False when there was none."""
        project = self._bound(name)
        kept = tuple(r for r in project.resources if r.key != key)
        if len(kept) == len(project.resources):
            return False
        self._save(replace(project, resources=kept, updated_at=time.time()))
        return True

    def select(self, name: str, key: str) -> Resource:
        """Make one resource the current one of its role."""
        project = self._bound(name)
        chosen = next((r for r in project.resources if r.key == key), None)
        if chosen is None:
            raise ValueError(f"No resource '{key}' on '{project.name}'.")
        resources = tuple(replace(r, current=(r.key == key)) if r.role == chosen.role else r for r in project.resources)
        self._save(replace(project, resources=resources, updated_at=time.time()))
        return replace(chosen, current=True)

    def append_log(self, name: str, entry: str) -> bool:
        """Add one timestamped line to a project's log. False if no project."""
        path = self._dir / f"{slugify(name)}.md"
        existing = self._read(path) if path.exists() else None
        if existing is None:
            return False
        # One log entry is one line: the reader keeps only lines beginning
        # "- ", so a newline in the entry would drop everything after it on the
        # next read — or, worse, a line the model wrote as "- foo" would be
        # parsed back as a separate forged log entry. Collapse to a single line.
        stamped = f"{_iso(time.time())}: {' '.join(entry.split())}"
        self._save(replace(existing, log=(*existing.log, stamped), updated_at=time.time()))
        return True

    # ── the index ────────────────────────────────────────────────────────────

    def index_prompt(self) -> str | None:
        """The one-line-per-project map for the system prompt."""
        projects = self.all()
        if not projects:
            return None
        lines = [
            "# Projects",
            "",
            "Ongoing work you keep durable state for. Only these one-line",
            "summaries are in your prompt — open a project with open_project",
            "(exact name below, or one of the names it is also called) before",
            "doing any work on it. Where its work lives is on the project.",
            "",
        ]
        # Working projects claim the budget first; done ones fill whatever is
        # left. Marking a project done must *free* index capacity — a recent
        # completion that could shadow an older active project would punish
        # exactly the hygiene the index message asks for.
        working = [p for p in projects if p.status != "done"]
        finished = [p for p in projects if p.status == "done"]
        shown = working[: self._max_index]
        shown += finished[: self._max_index - len(shown)]
        for project in shown:
            aliases = f" — also called {', '.join(project.aliases)}" if project.aliases else ""
            bound = f" — {len(project.resources)} resource{'s' if len(project.resources) != 1 else ''} bound" if project.resources else ""
            lines.append(
                f"- {project.name} ({project.status}): {project.description}"
                f"{aliases}{bound} — updated {_ago(project.updated_at)}"
            )
        hidden = len(projects) - len(shown)
        if hidden > 0:
            lines.append(f"- ({hidden} more not listed)")
        return "\n".join(lines)

    # ── internals ────────────────────────────────────────────────────────────

    def _save(self, project: Project) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        log_lines = "\n".join(f"- {line}" for line in project.log)
        # The description is one frontmatter line; a newline in it would be
        # reparsed as another key and could spoof status/timestamps. Collapse
        # it. State is intentionally multi-line and lives in the body, so it is
        # left alone.
        description = " ".join(project.description.split())
        # The bindings are one line each, after the fields every file has, so
        # a file from before they existed differs only by their absence.
        bindings = ""
        if project.id:
            bindings += f"id: {project.id}\n"
        if project.aliases:
            bindings += f"aliases: {', '.join(' '.join(a.split()) for a in project.aliases)}\n"
        for r in project.resources:
            flags = ("current " if r.current else "") + (f"open={r.opener} " if r.opener else "")
            bindings += f"resource.{r.key}: {r.role} {r.source} {flags}{' '.join(r.locator.split())}\n"
        text = (
            "---\n"
            f"name: {project.name}\n"
            f"description: {description}\n"
            f"status: {project.status}\n"
            f"created_at: {_iso(project.created_at)}\n"
            f"updated_at: {_iso(project.updated_at)}\n"
            f"{bindings}"
            "---\n\n"
            f"{_STATE_HEAD}"
            f"{project.state}"
            f"{_LOG_DELIM}"
            f"{log_lines}\n"
        )
        atomic_write(project.path, text)

    def _read(self, path: Path) -> Project | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            log.warning("could not read project %s", path)
            return None
        match = _FRONTMATTER.match(raw)
        if match is None:
            return None

        meta: dict[str, str] = {}
        for line in match.group(1).splitlines():
            key, sep, value = line.partition(":")
            if sep:
                meta[key.strip()] = value.strip()

        # Leading newlines only: trailing ones are part of the log delimiter
        # when the log is empty, and collapsing them would break the rfind.
        body = match.group(2).lstrip("\n")
        state, log_lines = "", []
        head = body.find(_STATE_HEAD)
        if head != -1:
            after_head = body[head + len(_STATE_HEAD):]
            # rindex, because the state may legitimately contain the log
            # delimiter — the real one is the last, appended by _save after
            # whatever the state holds.
            tail = after_head.rfind(_LOG_DELIM)
            if tail != -1:
                state = after_head[:tail]
                log_lines = [
                    line[2:].strip()
                    for line in after_head[tail + len(_LOG_DELIM):].splitlines()
                    if line.startswith("- ")
                ]
            else:
                # Hand-edited file without a log section: everything is state.
                state = after_head.strip()

        def parse_ts(value: str) -> float:
            try:
                return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                ).timestamp()
            except ValueError:
                return path.stat().st_mtime

        resources = []
        for key, value in meta.items():
            if not key.startswith("resource."):
                continue
            parsed = _parse_resource(key[len("resource."):], value)
            if parsed is not None:
                resources.append(parsed)
        return Project(
            name=meta.get("name", path.stem),
            description=meta.get("description", ""),
            status=meta.get("status", "active"),
            state=state,
            log=tuple(log_lines),
            created_at=parse_ts(meta.get("created_at", "")),
            updated_at=parse_ts(meta.get("updated_at", "")),
            path=path,
            id=meta.get("id", "").strip(),
            aliases=tuple(a.strip() for a in meta.get("aliases", "").split(",") if a.strip()),
            resources=tuple(resources),
        )


def _mint() -> str:
    return secrets.token_hex(16)


def _parse_resource(key: str, value: str) -> Resource | None:
    """``<role> <source> [current] [open=<app>] <locator…>``: the fixed words
    first so a locator may hold spaces; a line that does not fit is skipped
    with a warning rather than read as something it is not."""
    parts = value.split()
    if not _KEY.match(key) or len(parts) < 3 or parts[1] not in RESOURCE_SOURCES:
        log.warning("skipping a resource line that does not parse: resource.%s", key)
        return None
    role, source, rest = parts[0], parts[1], parts[2:]
    current, opener = False, ""
    while rest and (rest[0] == "current" or rest[0].startswith("open=")):
        token = rest.pop(0)
        if token == "current":
            current = True
        else:
            opener = token[len("open="):]
    if not rest:
        log.warning("skipping a resource line with no locator: resource.%s", key)
        return None
    return Resource(key, role, source, " ".join(rest), current, opener)


__all__ = ["ProjectStore", "Project", "Resolution", "Resource", "RESOURCE_SOURCES", "VALID_STATUSES"]
