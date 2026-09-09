"""Permission has a precise scope, and the store keeps it.

A private temporary store, a synthetic adapter namespace, and no model, mic,
network, or runtime state. Pins: a draft is saved and edited with its revision
and digest moving together, and is never executable; activation takes only an
attended private owner turn, the exact draft revision and digest, a registered
namespace, and limits within the configured caps, and commits the grant and
the mandate together; the runtime-only derive path refuses a mandate that is
paused, revoked, expired, at another revision, or belonging to another
adapter, refuses a child outside the grant's operations or targets, and
otherwise makes one queued child whose origin names its mandate, grant,
adapter, event, and source revision and nothing that claims attendance; the
same event at the same source revision returns the same child and spends
nothing; a newer source revision revises an unfinished child in place, keeps
the older revision as a replay alias, and voids its open question; a child
with dispatch intent is never revised; a change of scope is refused so the
adapter proposes instead; a finished child is never reopened, so the revision
derives a new one; lifetime and window allowances are enforced from the
persisted window start and survive reopening; revoking a grant ends its
mandate; an expired grant is marked so on the record; the owner's approval of
an exact proposal creates one task and marks the proposal in one transaction,
a stale proposal revision creates none, and a derived origin can never come
through create; the controller derives only through a registered namespace
and journals it; the owner view lists mandates and grants; a version-three
store is lifted to four with every origin saying it was human and a repeated
request still finding its task; and everything validates after reopening.

    uv run --no-sync python scripts/probe_task_authority.py
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from ciel.config import JournalConfig, TasksConfig
from ciel.journal import ActionJournal
from ciel.task_context import TaskBinding
from ciel.task_controls import TaskController
from ciel.tasks import (Criterion, DerivedOrigin, Evidence, GrantLimits, HumanOrigin, Namespace, Origin, RecordSet, RecordWrite, Scope,
                        Specification, Step, TaskConflict, TaskLimit, TaskStore)

CHECKS: list[str] = []
OWNER = 'fixture-owner'
TURN = HumanOrigin(OWNER, 'activation-turn', 'web', ingress_ids=('web:1',))
SCOPE = Scope(('calendar.create', 'inbox.read'), ('calendar:primary', 'inbox:main'))
LIMITS = GrantLimits(max_children=4, window_s=60.0, max_per_window=2, lifetime_s=3600.0)
NAMESPACE = Namespace('fixture-inbox', 1, lambda payload: None if isinstance(payload.get('status'), str) else (_ for _ in ()).throw(ValueError('status')))
OTHER = Namespace('fixture-other', 1, lambda payload: None)


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


async def refused(name: str, call: object, kind: type[Exception] = TaskConflict) -> None:
    try:
        await call
    except kind:
        check(name, True)
    else:
        check(name, False)


def config(directory: Path, **kwargs: object) -> TasksConfig:
    return replace(TasksConfig(), directory=directory, owner=OWNER, **kwargs)


def child_spec(target: str = 'calendar:primary', outcome: str = 'The dinner is on the calendar') -> Specification:
    return Specification(outcome, Scope(('calendar.create',), (target,)), (Criterion('exists', target, 'present', 'cal-1'),))


CREATE = Step('mutation', 'calendar.create', 'calendar:primary', (('when', 'friday 19:00'),))
LOOK = Step('read', 'calendar.create', 'calendar:primary')


def opened(directory: Path, **kwargs: object) -> TaskStore:
    store = TaskStore(config(directory, **kwargs))
    store.register(NAMESPACE)
    store.register(OTHER)
    return store


async def activate(store: TaskStore, now: float = 1000.0, limits: GrantLimits = LIMITS) -> tuple:
    draft = await store.save_grant_draft(OWNER, 'hub', SCOPE, (('account', 'me@example.test'),), now=now)
    return await store.activate_grant(TURN, draft.id, draft.revision, draft.digest, 'journal:approval-1',
                                      outcome='Confirmed dinners land on the calendar', namespace=NAMESPACE.name, limits=limits, now=now)


async def probe_drafts_and_activation(root: Path) -> None:
    print('\na draft is looked at; a grant is approved')
    async with opened(root / 'activation') as store:
        draft = await store.save_grant_draft(OWNER, 'hub', Scope(('inbox.read', 'calendar.create', 'inbox.read'), ('inbox:main', 'calendar:primary')),
                                             (('account', 'me@example.test'),), now=1000)
        check('a saved draft is normalized, revisioned, and never executable',
              draft.scope == SCOPE and draft.revision == 1 and draft.status == 'draft' and len(draft.digest) == 64)
        edited = await store.save_grant_draft(OWNER, 'hub', SCOPE, (('account', 'other@example.test'),), draft_id=draft.id, expected_revision=1, now=1001)
        check('editing the draft moves its revision and its digest together', edited.revision == 2 and edited.digest != draft.digest)
        await refused('an edit against a stale draft revision is refused', store.save_grant_draft(OWNER, 'hub', SCOPE, draft_id=draft.id, expected_revision=1))
        await refused('an approval of the revision the owner no longer sees leaves a draft, never a grant',
                      store.activate_grant(TURN, draft.id, 1, draft.digest, 'journal:1', outcome='x', namespace=NAMESPACE.name, limits=LIMITS))
        await refused('an approval whose digest differs from the draft is refused',
                      store.activate_grant(TURN, draft.id, 2, draft.digest, 'journal:1', outcome='x', namespace=NAMESPACE.name, limits=LIMITS))
        await refused('an unattended turn cannot activate a grant',
                      store.activate_grant(replace(TURN, attended=False), draft.id, 2, edited.digest, 'journal:1', outcome='x', namespace=NAMESPACE.name, limits=LIMITS))
        await refused('a derived origin cannot activate a grant',
                      store.activate_grant(DerivedOrigin(OWNER, 'r', 'm', 1, 'g', 1, NAMESPACE.name, 'e', 's', 1000.0, 'k'), draft.id, 2, edited.digest, 'journal:1',
                                           outcome='x', namespace=NAMESPACE.name, limits=LIMITS))
        await refused('a namespace no adapter registered cannot hold a mandate',
                      store.activate_grant(TURN, draft.id, 2, edited.digest, 'journal:1', outcome='x', namespace='nobody', limits=LIMITS),
                      Exception)
        await refused('limits above the configured caps are refused, not clamped silently',
                      store.activate_grant(TURN, draft.id, 2, edited.digest, 'journal:1', outcome='x', namespace=NAMESPACE.name,
                                           limits=replace(LIMITS, max_children=100000)), TaskLimit)
        check('nothing above made a grant', not await store.grants(OWNER) and not await store.mandates(OWNER))
        grant, mandate = await store.activate_grant(TURN, draft.id, 2, edited.digest, 'journal:approval-1',
                                                    outcome='Confirmed dinners land on the calendar', namespace=NAMESPACE.name, limits=LIMITS, now=1002)
        check('the approval commits the grant and the mandate together',
              grant.status == 'active' and grant.digest == edited.digest and grant.approval_ref == 'journal:approval-1'
              and grant.expires_at == 1002 + LIMITS.lifetime_s and mandate.status == 'active' and mandate.grant_id == grant.id
              and mandate.namespace == NAMESPACE.name and mandate.children == 0)
        drafts = await store.grant_drafts(OWNER)
        check('the draft is marked activated and cannot be approved twice', drafts[0].status == 'activated')
        await refused('a second approval of the same draft makes nothing',
                      store.activate_grant(TURN, draft.id, drafts[0].revision, edited.digest, 'journal:2', outcome='x', namespace=NAMESPACE.name, limits=LIMITS))
        view = await store.owner_view(OWNER)
        check('the owner view lists mandates and grants beside tasks',
              view['mandates'][0]['id'] == mandate.id and view['grants'][0]['id'] == grant.id and view['tasks'] == [])
        check('another owner sees neither', (await store.owner_view('another'))['mandates'] == [] and (await store.owner_view('another'))['grants'] == [])
    async with opened(root / 'activation') as store:
        check('grant, mandate, and activated draft validate after reopening',
              (await store.grants(OWNER))[0].id == grant.id and (await store.mandates(OWNER))[0].revision == 1)


async def probe_derivation(root: Path) -> None:
    print('\nderived work inherits; it never invents')
    async with opened(root / 'derive') as store:
        grant, mandate = await activate(store)
        await refused('a child outside the grant\'s operations is refused',
                      store.derive_task(OWNER, mandate.id, mandate.revision, NAMESPACE.name, 'evt-1', 'src-1',
                                        Specification('x', Scope(('calendar.delete',), ('calendar:primary',)), (Criterion('c', 'calendar:primary', 'gone'),)),
                                        Step('mutation', 'calendar.delete', 'calendar:primary'), now=1003))
        await refused('a child outside the grant\'s targets is refused',
                      store.derive_task(OWNER, mandate.id, mandate.revision, NAMESPACE.name, 'evt-1', 'src-1', child_spec('calendar:work'),
                                        replace(CREATE, target='calendar:work'), now=1004))
        await refused('another adapter cannot derive under this mandate',
                      store.derive_task(OWNER, mandate.id, mandate.revision, OTHER.name, 'evt-1', 'src-1', child_spec(), CREATE, now=1005))
        await refused('a stale mandate revision is refused',
                      store.derive_task(OWNER, mandate.id, mandate.revision + 1, NAMESPACE.name, 'evt-1', 'src-1', child_spec(), CREATE, now=1006))
        await refused('another owner cannot derive under it',
                      store.derive_task('another', mandate.id, mandate.revision, NAMESPACE.name, 'evt-1', 'src-1', child_spec(), CREATE, now=1007))
        check('no refusal spent anything', (await store.mandates(OWNER))[0].children == 0)
        child = await store.derive_task(OWNER, mandate.id, mandate.revision, NAMESPACE.name, 'evt-1', 'src-1', child_spec(), CREATE, now=1010)
        origin = child.origin
        check('a derived child is queued with an origin that names what it inherits and claims no attendance',
              child.status == 'queued' and isinstance(origin, DerivedOrigin) and origin.kind == 'derived'
              and origin.parent_mandate_id == mandate.id and origin.grant_id == grant.id and origin.grant_revision == grant.revision
              and origin.adapter_namespace == NAMESPACE.name and origin.event_key == 'evt-1' and origin.source_revision == 'src-1'
              and not hasattr(origin, 'attended') and not hasattr(origin, 'lane'))
        check('the child carries its own allowances and counts against the parent',
              child.max_polls == TasksConfig().max_polls and (await store.mandates(OWNER))[0].children == 1)
        check('the runner would find the child eligible like any finite task', (await store.eligible(OWNER, now=1011))[0].id == child.id)
        again = await store.derive_task(OWNER, mandate.id, mandate.revision, NAMESPACE.name, 'evt-1', 'src-1', child_spec(), CREATE, now=1012)
        check('a replay of the same event and source revision returns the child and spends nothing',
              again.id == child.id and again.revision == child.revision and (await store.mandates(OWNER))[0].children == 1)
        question = await store.ask_owner(OWNER, child.id, child.revision, 'Which calendar?', ('primary', 'work'), now=1013)
        revised = await store.derive_task(OWNER, mandate.id, mandate.revision, NAMESPACE.name, 'evt-1', 'src-2',
                                          child_spec(outcome='The dinner is on the calendar at eight'), replace(CREATE, arguments=(('when', 'friday 20:00'),)), now=1014)
        check('a newer source revision revises the unfinished child in place and requeues it',
              revised.id == child.id and revised.revision == question.revision + 1 and revised.status == 'queued'
              and revised.specification.outcome.endswith('eight') and revised.next_step.arguments == (('when', 'friday 20:00'),)
              and revised.origin.source_revision == 'src-2' and revised.origin.request_id == origin.request_id)
        check('the revision voids the open question', (await store.owner_view(OWNER, child.id))['question'] is None)
        check('the revision spends no parent allowance', (await store.mandates(OWNER))[0].children == 1)
        old = await store.derive_task(OWNER, mandate.id, mandate.revision, NAMESPACE.name, 'evt-1', 'src-1', child_spec(), CREATE, now=1015)
        check('replaying the older revision lands on the alias and rolls nothing back',
              old.id == child.id and old.revision == revised.revision and old.origin.source_revision == 'src-2')
        check('every trigger the mandate has seen is on the record',
              [(e, s) for e, s, _ in await store.derivations(OWNER, mandate.id)] == [('evt-1', 'src-1'), ('evt-1', 'src-2')])
        await refused('a revision that changes the scope is a proposal, never a revision',
                      store.derive_task(OWNER, mandate.id, mandate.revision, NAMESPACE.name, 'evt-1', 'src-3',
                                        Specification('x', Scope(('calendar.create', 'inbox.read'), ('calendar:primary',)),
                                                      (Criterion('exists', 'calendar:primary', 'present'),)), CREATE, now=1015))
        attempt = await store.claim(OWNER, child.id, revised.revision, now=1016)
        await refused('a child mid-step is not revised', store.derive_task(OWNER, mandate.id, mandate.revision, NAMESPACE.name, 'evt-1', 'src-3', child_spec(), CREATE, now=1017))
        attempt = await store.mark_dispatched(OWNER, attempt, now=1017)
        frozen = await store.abandon(OWNER, attempt, 'lost the response', eligible_at=1018, now=1018)
        check('a sent mutation with a lost response waits for reconciliation', frozen.status == 'waiting' and frozen.wait_reason == 'reconciliation')
        await refused('a child with dispatch intent is never revised in place',
                      store.derive_task(OWNER, mandate.id, mandate.revision, NAMESPACE.name, 'evt-1', 'src-3', child_spec(), CREATE, now=1019))
        check('the refused revision left no alias', len(await store.derivations(OWNER, mandate.id)) == 2)
        done = await store.derive_task(OWNER, mandate.id, mandate.revision, NAMESPACE.name, 'evt-2', 'src-1', child_spec(), LOOK, now=1020)
        attempt = await store.claim(OWNER, done.id, done.revision, now=1021)
        attempt = await store.mark_dispatched(OWNER, attempt, now=1021)
        seen = await store.observe(OWNER, attempt, (Evidence('exists', 'calendar:primary', 'present', 'fixture', 1022, 'cal-1'),), now=1022)
        done = await store.complete(OWNER, done.id, seen.revision, now=1023)
        successor = await store.derive_task(OWNER, mandate.id, mandate.revision, NAMESPACE.name, 'evt-2', 'src-2', child_spec(), LOOK, now=1070)
        check('a finished child is never reopened; the revision derives a new one',
              done.status == 'done' and successor.id != done.id and successor.status == 'queued'
              and (await store.get(OWNER, done.id)).status == 'done' and (await store.mandates(OWNER))[0].children == 3)
        check('the finished child\'s own trigger still replays to it',
              (await store.derive_task(OWNER, mandate.id, mandate.revision, NAMESPACE.name, 'evt-2', 'src-1', child_spec(), LOOK, now=1071)).id == done.id)
    async with opened(root / 'derive') as store:
        check('derived tasks, aliases, and counters validate after reopening',
              (await store.get(OWNER, child.id)).origin.source_revision == 'src-2' and (await store.mandates(OWNER))[0].children == 3)


async def probe_allowances_and_controls(root: Path) -> None:
    print('\nallowances, controls, revocation, expiry')
    async with opened(root / 'allowances') as store:
        grant, mandate = await activate(store, now=2000)
        for n in (1, 2):
            await store.derive_task(OWNER, mandate.id, mandate.revision, NAMESPACE.name, f'evt-{n}', 'src-1', child_spec(), LOOK, now=2001 + n)
        await refused('the window allowance refuses a third derivation inside the window',
                      store.derive_task(OWNER, mandate.id, mandate.revision, NAMESPACE.name, 'evt-3', 'src-1', child_spec(), LOOK, now=2010), TaskLimit)
        check('a refused derivation leaves the counters as they were', (await store.mandates(OWNER))[0].window_count == 2)
    async with opened(root / 'allowances') as store:
        state = (await store.mandates(OWNER))[0]
        check('the window start survives reopening; a restart refills nothing', state.window_start == 2000 and state.window_count == 2)
        third = await store.derive_task(OWNER, mandate.id, mandate.revision, NAMESPACE.name, 'evt-3', 'src-1', child_spec(), LOOK, now=2000 + 130)
        state = (await store.mandates(OWNER))[0]
        check('a new window opens on the persisted boundary, not at the moment of the call',
              third.status == 'queued' and state.window_start == 2120 and state.window_count == 1 and state.children == 3)
    async with opened(root / 'allowances', max_grant_per_window=1) as store:
        await refused('a lowered configured cap governs an already approved grant',
                      store.derive_task(OWNER, mandate.id, mandate.revision, NAMESPACE.name, 'evt-4', 'src-1', child_spec(), LOOK, now=2131), TaskLimit)
    async with opened(root / 'allowances') as store:
        await store.derive_task(OWNER, mandate.id, mandate.revision, NAMESPACE.name, 'evt-4', 'src-1', child_spec(), LOOK, now=2131)
        await refused('the lifetime allowance is exhausted at the grant\'s number',
                      store.derive_task(OWNER, mandate.id, mandate.revision, NAMESPACE.name, 'evt-5', 'src-1', child_spec(), LOOK, now=2300), TaskLimit)
    async with opened(root / 'controls') as store:
        grant, mandate = await activate(store, now=3000)
        paused = await store.mandate_control(OWNER, mandate.id, 'pause', revision=mandate.revision, now=3001)
        await refused('a paused mandate derives nothing',
                      store.derive_task(OWNER, mandate.id, paused.revision, NAMESPACE.name, 'evt-1', 'src-1', child_spec(), LOOK, now=3002))
        await refused('a Chart control carries the revision it rendered', store.mandate_control(OWNER, mandate.id, 'resume', revision=mandate.revision, now=3002))
        resumed = await store.mandate_control(OWNER, mandate.id, 'resume', now=3003)
        child = await store.derive_task(OWNER, mandate.id, resumed.revision, NAMESPACE.name, 'evt-1', 'src-1', child_spec(), LOOK, now=3004)
        check('a resumed mandate derives again at its new revision', child.status == 'queued' and resumed.revision == paused.revision + 1)
        revoked = await store.revoke_grant(OWNER, grant.id, now=3005)
        ended = (await store.mandates(OWNER))[0]
        check('revoking the grant ends its mandate in the same transaction',
              revoked.status == 'revoked' and revoked.revoked_at == 3005 and ended.status == 'revoked' and ended.detail == 'grant revoked')
        await refused('a revoked mandate derives nothing', store.derive_task(OWNER, mandate.id, ended.revision, NAMESPACE.name, 'evt-2', 'src-1', child_spec(), LOOK, now=3006))
        await refused('a revoked mandate cannot resume', store.mandate_control(OWNER, mandate.id, 'resume', now=3007))
        check('the child already derived keeps its own state', (await store.get(OWNER, child.id)).status == 'queued')
        await refused('revocation is applied once', store.revoke_grant(OWNER, grant.id, now=3008))
    async with opened(root / 'expiry') as store:
        grant, mandate = await activate(store, now=4000, limits=replace(LIMITS, lifetime_s=100.0))
        await refused('an expired grant derives nothing', store.derive_task(OWNER, mandate.id, mandate.revision, NAMESPACE.name, 'evt-1', 'src-1', child_spec(), LOOK, now=4100))
        expired = (await store.grants(OWNER))[0]
        check('the expiry is marked on the record even though the derivation was refused',
              expired.status == 'expired' and (await store.mandates(OWNER))[0].status == 'expired')
    async with opened(root / 'expiry') as store:
        check('an expired grant validates after reopening', (await store.grants(OWNER))[0].status == 'expired')


async def probe_approval_path(root: Path) -> None:
    print('\nonly the owner\'s approval of the exact proposal becomes a task')
    async with opened(root / 'approval') as store:
        proposal = RecordSet(NAMESPACE.name, (RecordWrite('proposal:evt-1', {'status': 'open'}, 0),))
        await store.write_records(OWNER, proposal)
        approving = HumanOrigin(OWNER, 'approval-turn', 'web', ingress_ids=('web:9',), approval_ref='journal:proposal-evt-1:1')
        approved = RecordSet(NAMESPACE.name, (RecordWrite('proposal:evt-1', {'status': 'approved'}, 1),))
        task = await store.create(approving, child_spec(), LOOK, records=approved, now=5000)
        record = (await store.records(OWNER, NAMESPACE.name, ('proposal:evt-1',)))[0]
        check('the approval creates one task and marks the proposal in one transaction',
              task.origin.approval_ref == 'journal:proposal-evt-1:1' and record.payload == {'status': 'approved'} and record.revision == 2)
        stale = HumanOrigin(OWNER, 'approval-turn-2', 'web', ingress_ids=('web:10',), approval_ref='journal:proposal-evt-1:1')
        await refused('a stale proposal revision creates no task', store.create(stale, child_spec(), LOOK, records=approved, now=5001))
        check('the refused approval left no task behind', len(await store.list(OWNER)) == 1)
        await refused('a derived origin never comes through create',
                      store.create(DerivedOrigin(OWNER, 'r', 'm', 1, 'g', 1, NAMESPACE.name, 'e', 's', 5000.0, 'k'), child_spec(), LOOK))
        await refused('an unattended turn still cannot create', store.create(replace(approving, attended=False), child_spec(), LOOK))
    async with opened(root / 'approval') as store:
        check('the approval reference survives reopening', (await store.get(OWNER, task.id)).origin.approval_ref == 'journal:proposal-evt-1:1')


async def probe_controller(root: Path) -> None:
    print('\nthe controller derives only through a registered adapter')
    journal_dir = root / 'journal'
    journal = ActionJournal(JournalConfig(dir=journal_dir))
    controller = TaskController(config(root / 'controller', enabled=True), journal, namespaces=(NAMESPACE,))
    await controller.start()
    store = controller.store
    assert store is not None
    grant, mandate = await activate(store, now=6000)
    await refused('a namespace the controller did not register cannot derive',
                  controller.derive(OTHER, mandate.id, mandate.revision, 'evt-1', 'src-1', child_spec(), LOOK, now=6001))
    task = await controller.derive(NAMESPACE, mandate.id, mandate.revision, 'evt-1', 'src-1', child_spec(), LOOK, now=6001)
    entries = journal.recent(5)
    check('a derivation through the controller is journaled without dispatching anything',
          task.status == 'queued' and entries[-1]['tool'] == 'task_derive' and 'no execution' in entries[-1]['note'])
    binding = TaskBinding(Origin(OWNER, 'chart-turn', 'web', ingress_ids=('web:2',)), 1, 1)
    result = await controller.apply(binding, 'mandate_pause', {'mandate_id': mandate.id}, revision=mandate.revision)
    check('the Chart door pauses a mandate with the same owner admission as a task control',
          result['mandate']['status'] == 'paused' and journal.recent(1)[0]['tool'] == 'task_mandate_pause')
    stranger = TaskBinding(Origin('someone', 'chart-turn', 'web', ingress_ids=('web:3',)), 1, 1)
    await refused('another owner\'s binding controls nothing', controller.apply(stranger, 'mandate_resume', {'mandate_id': mandate.id}))
    result = await controller.apply(binding, 'grant_revoke', {'grant_id': grant.id})
    check('the Chart door revokes a grant without waiting', result['grant']['status'] == 'revoked')
    await controller.close()


async def probe_migration(root: Path) -> None:
    print('\nversion three is lifted, not reset')
    cfg = config(root / 'schema')
    async with opened(root / 'schema') as store:
        task = await store.create(Origin(OWNER, 'request-1', 'voice', ingress_ids=('voice:1',)), child_spec(), LOOK, now=7000)
    check('this SQLite can build a version-three fixture', sqlite3.sqlite_version_info >= (3, 35))
    db = sqlite3.connect(cfg.directory / 'tasks.sqlite3')
    for row in db.execute('SELECT id,request_json FROM tasks').fetchall():
        request = json.loads(row[1])
        request['origin'] = {k: v for k, v in request['origin'].items() if k not in ('kind', 'approval_ref')}
        db.execute('UPDATE tasks SET request_json=? WHERE id=?', (json.dumps(request, sort_keys=True, separators=(',', ':')), row[0]))
    db.executescript('DROP TABLE derivations; DROP TABLE mandates; DROP TABLE grants; DROP TABLE grant_drafts; PRAGMA user_version=3;')
    db.commit()
    db.close()
    async with opened(root / 'schema') as store:
        lifted = await store.get(OWNER, task.id)
        db = sqlite3.connect(cfg.directory / 'tasks.sqlite3')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        db.close()
        check('a version-three store opens as version four with every task in place and its origin saying it was human',
              version == 4 and lifted.status == 'queued' and lifted.origin == task.origin and lifted.origin.kind == 'human')
        same = await store.create(Origin(OWNER, 'request-1', 'voice', ingress_ids=('voice:1',)), child_spec(), LOOK, now=7001)
        check('a repeated request still finds its migrated task', same.id == task.id)
        grant, mandate = await activate(store, now=7002)
        check('a lifted store holds authority like a fresh one', mandate.status == 'active')


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix='ciel-authority-probe-') as tmp:
        root = Path(tmp)
        await probe_drafts_and_activation(root)
        await probe_derivation(root)
        await probe_allowances_and_controls(root)
        await probe_approval_path(root)
        await probe_controller(root)
        await probe_migration(root)
    print(f'\nall {len(CHECKS)} checks passed')


if __name__ == '__main__':
    asyncio.run(main())
