"""Review fixture: temporary loopback server, fake accounts, scripted replies.

Updated 2026-09-05 after the fixes: both sockets are now closed by the
server with 4401, and no recording is accepted afterwards."""
import asyncio, tempfile, struct
from dataclasses import replace
from pathlib import Path
from aiohttp import web
from aiohttp.test_utils import TestServer,TestClient
from ciel.config import Config
from ciel.interview.app import InterviewApp
async def until(ws,pred):
    async with asyncio.timeout(5):
        while True:
            f=await ws.receive_json()
            if pred(f): return f
async def main():
    with tempfile.TemporaryDirectory() as tmp:
        cfg=replace(Config(),interview=replace(Config().interview,dir=Path(tmp)))
        room=InterviewApp(cfg,dev=True); room._secret='fixture-secret'
        room.accounts.create('alice',password='password-one')
        app=web.Application();room.register(app.router)
        async with TestClient(TestServer(app)) as client:
            await client.post('/interview/api/login',json={'username':'alice','password':'password-one'})
            r=await client.post('/interview/api/sessions',json={'mode':'company','length_min':15})
            sid=(await r.json())['session']['id']
            ws=await client.ws_connect('/interview/ws?session='+sid)
            await ws.send_json({'type':'hello','v':1,'caps':{'tts':'none'}})
            await until(ws,lambda f:f['type']=='turn.end')
            r=await client.post('/interview/api/password',json={'current':'password-one','new':'password-two'})
            try:
                await ws.send_json({'type':'typed','text':'I led a complex migration.'})
                f=await until(ws,lambda f:f['type']=='turn.end')
                print('OLD WEBSOCKET AFTER SELF RESET: still receives next model turn',f)
            except Exception as exc:
                print('OLD WEBSOCKET AFTER SELF RESET: closed by the server with',ws.close_code,'('+type(exc).__name__+')')
            # Updated 2026-09-05: the reset above ends the interview and closes
            # its socket, so the disable is tried on a fresh one.
            await client.post('/interview/api/login',json={'username':'alice','password':'password-two'})
            r=await client.post('/interview/api/sessions',json={'mode':'company','length_min':15})
            sid=(await r.json())['session']['id']
            ws=await client.ws_connect('/interview/ws?session='+sid)
            await ws.send_json({'type':'hello','v':1,'caps':{'tts':'none'}})
            await until(ws,lambda f:f['type']=='turn.end')
            room.accounts.set_disabled('alice',True)
            await room._evict(room.accounts.get('alice').id,'disabled')
            try:
                await until(ws,lambda f:f['type']=='debrief.ready')
                await ws.send_bytes(struct.pack('>I',1)+b'POST_DISABLE_AUDIO')
            except Exception as exc:
                print('  (the socket was closed before the debrief:',ws.close_code,type(exc).__name__+')')
            path=room._store.recording_path('alice',sid)
            await asyncio.sleep(.2)
            print('DISABLED WEBSOCKET: closed=',ws.closed,'accepted recording=',path.exists() and path.read_bytes()==b'POST_DISABLE_AUDIO')
            await ws.close()
        await room.close()
asyncio.run(main())
