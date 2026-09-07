"""Review fixtures: temporary data, fake sockets and SDK; no live model calls.

Updated 2026-09-05 after the fixes to the room's changed signatures
(briefs and sessions take the account, evictions the account id,
reservations are objects). Expected output now: old transcript
accessible False; old session live False, socket closed True; session
ended True, socket closed True, audio appended False; midnight frees
current day False; drain timeout debt cleared True but the connection
retired and the new answer refused for the right reason.
"""
import asyncio, contextlib, importlib.util, tempfile, time
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
from ciel.config import Config
from ciel.interview.app import InterviewApp
from ciel.interview.accounts import sign_cookie
from ciel.interview.brain import AgentSdkBackend
import ciel.interview.brain as brain

class Request:
    def __init__(self, room, account, body=None, sid=''):
        self.cookies={room._cfg.cookie_name:sign_cookie(room._secret,account,time.time()+3600)}
        self.body=body or {}; self.match_info={'id':sid}; self.headers={}; self.secure=False
    async def json(self): return self.body
class WS:
    closed=False
    def __init__(self): self.frames=[]
    async def send_str(self,text): self.frames.append(text)
    async def close(self,*args,**kw): self.closed=True

async def main():
    with tempfile.TemporaryDirectory() as temp:
        cfg=replace(Config(),interview=replace(Config().interview,dir=Path(temp),daily_sessions_per_user=1))
        room=InterviewApp(cfg,dev=True); room._secret='fixture-secret'
        room.accounts.create('alice',password='password-one')
        old=room.accounts.get('alice')
        setup={'mode':'company','length_min':15}
        brief=await room._generate_brief(setup,old)
        meta=room._store.create('alice','company',setup,brief,owner_id=old.id)
        sid=meta['id']
        room._store.append_transcript('alice',sid,1,'candidate','PRIVATE ORIGINAL ACCOUNT ANSWER')
        room.accounts.delete('alice'); room.accounts.create('alice',password='password-two')
        replacement=room.accounts.get('alice')
        try:
            resp=await room._session_get(Request(room,replacement,sid=sid)); accessible=b'PRIVATE ORIGINAL' in resp.body
        except Exception as exc:  # HTTPNotFound: not hers
            accessible=False; print('  (session_get as the replacement raised', type(exc).__name__+')')
        print('RECREATED ACCOUNT: different identity:',old.id != replacement.id,'; old transcript accessible:',accessible)

        meta=room._store.create('alice','company',setup,brief,owner_id=replacement.id); sid=meta['id']
        session=room._open_session(replacement,sid,meta); ws=WS()
        await session.attach(ws,{'tts':'none'})
        for _ in range(100):
            if session.state in ('speaking','listening'): break
            await asyncio.sleep(.01)
        req=Request(room,replacement,{'current':'password-two','new':'password-three'})
        response=await room._change_password(req)
        print('SELF RESET: response:',response.status,'; old HTTP cookie denied:',room._user(req) is None,'; old session live:',session.live,'; socket closed:',ws.closed)
        room.accounts.set_disabled('alice',True)
        await room._evict(replacement.id,'disabled fixture')
        session.on_audio(1,b'AFTER_ACCOUNT_DISABLED')
        path=room._store.recording_path('alice',sid)
        print('EVICT: session ended:',session._ended,'; socket closed:',ws.closed,'; audio appended after disable:',path.exists() and path.read_bytes()==b'AFTER_ACCOUNT_DISABLED')
        await session.close()

        day=['2026-09-05']; room._today=lambda:day[0]
        slot=room._reserve('alice','sessions')
        day[0]='2026-09-06'; room._reserve('alice','sessions')
        room._release(slot)
        print('MIDNIGHT: releasing previous day request frees current day quota:',room._store.usage('alice','2026-09-06','sessions')==0)

    spec=importlib.util.spec_from_file_location('probe',str(Path(__file__).resolve().parents[2] / 'scripts/probe_interview.py'))
    p=importlib.util.module_from_spec(spec); spec.loader.exec_module(p)
    class DelayedInterrupt(p._FakeClient):
        async def interrupt(self):
            if self._task:
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError): await self._task
                self._task=None
            self.late=self._result(len(self.turns),is_error=True,errors=['stale aborted turn'])
    client=DelayedInterrupt(); backend=AgentSdkBackend(Config().interview); backend._client=client
    async def collect(text): return ''.join([part async for part in backend.ask(text)])
    reader=asyncio.create_task(collect('old answer')); await asyncio.sleep(.005); reader.cancel()
    with contextlib.suppress(asyncio.CancelledError): await reader
    with patch.object(brain,'_DRAIN_S',.02): await backend.interrupt()
    print('DRAIN TIMEOUT: in-flight debt cleared:',not backend._in_flight,'; connection retired:',backend._client is None)
    client.queue.put_nowait(client.late)
    try:
        await collect('new answer')
        print('DRAIN TIMEOUT: new answer unexpectedly succeeded')
    except brain.BackendError as exc:
        print('DRAIN TIMEOUT: new answer consumed old error:',str(exc))
    await backend.close()

asyncio.run(main())
