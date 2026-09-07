"""Review reproductions using temporary data and fake audio; no model calls.

Run from the repository root:
    PYTHONPATH=src .venv/bin/python reports/2026-09-04-review-repros.py

These print observed behavior rather than asserting success. As first
written (2026-09-04) every line reported the defect; after the fixes of
the same day each line should read False / [200, 429] / a snapshot present
/ nothing played. The regression checks proper live in the probes
(probe_interview auth+brief, probe_hub_arbiter, probe_spoke, probe_files).
"""
import asyncio, importlib.util, json, tempfile, time, threading, subprocess
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
from ciel.config import Config, WebConfig
from ciel.interview.app import InterviewApp, COOKIE
from ciel.interview.accounts import Accounts, sign_cookie
import ciel.interview.accounts as accounts_mod
from ciel.hub.server import HubServer
from ciel.remote.web import Admission
from ciel.brain.permissions import WorkspaceGuard

class Request:
    def __init__(self, cookie='', body=None):
        self.cookies={COOKIE:cookie}; self.body=body or {}
    async def json(self): return self.body

async def main():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        cfg=replace(Config(), interview=replace(Config().interview, dir=root/'iv', daily_sessions_per_user=1))
        app=InterviewApp(cfg); app._secret='test-secret'
        app.accounts.create('alice', password='old-password')
        cookie=sign_cookie(app._secret,app.accounts.get('alice'),time.time()+3600)
        req=Request(cookie)
        app.accounts.reset('alice', 'new-password')
        print('RESET: old cookie still admitted:', app._user(req) is not None)
        app.accounts.delete('alice'); app.accounts.create('alice', role='admin', password='third-password')
        recreated=app._user(req)
        print('RECREATE: old cookie gains new account role:', recreated.role if recreated else None)
        cookie=sign_cookie(app._secret,app.accounts.get('alice'),time.time()+3600)
        req=Request(cookie)

        entered=threading.Event(); release=threading.Event(); original=accounts_mod._hash
        def held_hash(password, salt):
            entered.set(); release.wait(5); return original(password, salt)
        with patch.object(accounts_mod, '_hash', held_hash):
            job=asyncio.create_task(asyncio.to_thread(app.accounts.reset,'alice','fourth-password'))
            await asyncio.to_thread(entered.wait, 5)
            app.accounts.set_disabled('alice', True)
            release.set(); await job
        print('RACE: concurrent reset undoes disable:', not app.accounts.get('alice').disabled)
        app.accounts.set_disabled('alice', False)
        req=Request(sign_cookie(app._secret,app.accounts.get('alice'),time.time()+3600))  # the reset retired the last one

        started=0
        async def generate(setup, username):
            nonlocal started
            started+=1
            await asyncio.sleep(0.2)  # both requests are in flight before either finishes
            return {'company':{'name':'Fixture'}}
        app._generate_brief=generate
        responses=await asyncio.gather(app._sessions_create(req),app._sessions_create(req))
        print('DAILY CAP=1: parallel creates statuses/count:', [r.status for r in responses],len(app._store.list('alice')))
        for meta in app._store.list('alice'): app._store.delete('alice',meta['id'])
        print('DAILY CAP: deleting sessions resets usage:', app._reserve('alice','sessions'))

        server=HubServer(WebConfig())
        timers=[{'id':'t1','kind':'timer','due_at':time.time()+60,'label':'','duration_s':60,'pending':False}]
        server.note_timers(timers)
        queue,_=server._welcome(object(),Admission(True,role='spoke',client_id='test'))
        server.note_timers(timers)
        frames=[]
        while not queue.empty(): frames.append(json.loads(queue.get_nowait()))
        print('RECONNECT: fresh spoke receives frame types:',[f['type'] for f in frames],'; timer snapshot:',any('timers' in f for f in frames))

        workspace=root/'workspace'; workspace.mkdir()
        secret=workspace/'credentials.json'; secret.write_text('{"review_fixture":"FAKE_REVIEW_SECRET"}')
        guard=WorkspaceGuard(workspace)
        direct=await guard({'tool_name':'Read','tool_input':{'file_path':str(secret)}},None,None)
        recursive=await guard({'tool_name':'Grep','tool_input':{'path':str(workspace),'pattern':'review_fixture','output_mode':'content'}},None,None)
        fixture_found=subprocess.run(['rg','review_fixture',str(workspace)],capture_output=True,text=True).returncode==0
        print('GUARD: direct secret Read denied / recursive Grep allowed / fixture matches:', bool(direct),not recursive,fixture_found)
        from ciel.brain.tools import files as file_tools
        file_tools.bind_files(guard)
        sieved=(await file_tools.search_files.handler({'pattern':'review_fixture','path':str(workspace)}))['content'][0]['text']
        print('GUARD: search_files shows the fixture:', 'FAKE_REVIEW_SECRET' in sieved)

        spec=importlib.util.spec_from_file_location('probe_spoke',str(Path(__file__).resolve().parents[1] / 'scripts/probe_spoke.py'))
        probe=importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)
        spoke=probe.make_spoke(connected=False)
        spoke._state=probe.State.WAITING
        spoke._set_muted(True)
        spoke._timers.set_local(1,time.time()-5)
        due=spoke._timers.due(time.time(),hub_connected=False)
        await spoke._ring_locally(due)
        print('MUTE: due offline timer still played:',spoke._player.played)

asyncio.run(main())
