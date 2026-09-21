"""Probe planning and strict approvals through real private sockets.

Two authenticated pages share a temporary diary. The broker's questions are
addressed only to their initiating socket and excluded from replay. Another
page, generic chat, missing IDs, stale digests, disconnect and reconnect cannot
approve a bulk change. Its receipt survives a lost reply; Undo needs a new
scope. Library and plan controls use the same page path as ordinary meals.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import aiohttp
from ciel.confirm import VoiceConfirmBroker
from ciel.nutrition import NutritionController
from ciel.remote.web import WebLink
from probe_nutrition import context, meal
from probe_nutrition_library import fixture

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name);print(f"  {'ok  ' if ok else 'FAIL'} {name}",flush=True)
    if not ok:sys.exit(1)


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ciel-planning-wire-") as tmp,patch.object(Path,"home",return_value=Path(tmp)):
        cfg=fixture(Path(tmp));cfg=replace(cfg,web=replace(cfg.web,port=0,confirm_timeout_s=1.0))
        c=NutritionController(cfg,host="fixture-host");await c.start()
        original=await c.apply(context(cfg),"save",{**meal(),"items":[{**meal()["items"][0],"source":"estimate"}]})
        c.config=replace(cfg,nutrition=replace(cfg.nutrition,estimate_allowance_pct=20))
        broker=VoiceConfirmBroker(cfg);link=WebLink(cfg.web,cfg.hub);link.bind_nutrition(c,broker);await link.start()
        url=f"http://127.0.0.1:{link._site._server.sockets[0].getsockname()[1]}/ws"
        seen: dict[int,list[dict[str,Any]]] = {}
        async def receive(ws: Any,kind: str,identity: str | None=None) -> Any:
            while True:
                frame=await ws.receive_json(timeout=3)
                seen.setdefault(id(ws),[]).append(frame)
                if frame["type"]==kind and (identity is None or frame.get("request_id")==identity):return frame
        async def send(ws: Any,identity: str,op: str,data: dict[str,Any]) -> None:
            await ws.send_json({"type":"nutrition.request","request_id":identity,"operation":op,"data":data})
        async def call(ws: Any,identity: str,op: str,data: dict[str,Any]) -> Any:
            await send(ws,identity,op,data);return await receive(ws,"nutrition.result",identity)
        async def connect(session: Any) -> Any:
            ws=await session.ws_connect(url);await ws.send_json({"type":"hello","v":1,"role":"chart","token":"fixture-token","client_id":"same-public-client-id"});await receive(ws,"hello");return ws
        async def answer(ws: Any,identity: str,q: Any,**changes: Any) -> Any:
            await ws.send_json({"type":"nutrition.answer","request_id":identity,"confirm_id":q["confirm_id"],"operation":q["operation"],"digest":q["digest"],"approve":True,**changes})
            return await receive(ws,"nutrition.result",identity)
        async def review(ws: Any,identity: str) -> Any:
            r=await call(ws,identity,"bulk_preview",{"from":"2026-09-13","to":"2026-09-13"});assert r["ok"];return {"preview_id":r["data"]["preview_id"],"digest":r["data"]["digest"]}
        try:
            async with aiohttp.ClientSession() as session:
                a,b=await connect(session),await connect(session)
                saved=await call(a,"food","catalog_save",{"kind":"food","name":"Usual oats","revision":0,"items":meal()["items"]})
                check("a private page saves a named food through the controller",saved["ok"] and "Saved food" in saved["data"]["text"])
                entries=(await call(a,"catalog","catalog",{}))["data"]["entries"]
                portion={"source_kind":"food","source_id":entries[0]["id"],"source_revision":1}
                preview=await call(a,"preview","preview",{**portion,"day":"2026-09-13"})
                check("a socket preview separates actual and projected totals",preview["data"]["logged"]["calories"]==220 and preview["data"]["projected"]["calories"]==420)
                saved_plan=await call(a,"plan","plan_save",{**portion,"day":"2026-09-13","revision":0})
                check("the socket plan remains outside intake",saved_plan["ok"] and (await call(a,"day","day",{"day":"2026-09-13"}))["data"]["totals"]["calories"]==220)
                scope=await review(a,"review")
                await send(a,"bulk","bulk_apply",scope);q=(await receive(a,"nutrition.question"))["data"]
                check("the page receives the broker's exact reviewed scope",q["digest"]==scope["digest"] and q["review"]["count"]==1 and q["expires_at"]>time.time())
                check("a generic keyboard yes cannot approve the socket's question",not broker.answer("yes"))
                await b.send_json({"type":"say","text":"yes"})
                check("ordinary chat cannot carry the strict question identity",not broker.answer("yes") and broker._scope is not None)
                other=await answer(b,"other-answer",q)
                check("a second tab with the same advertised client ID cannot approve",not other["data"]["accepted"] and broker._scope is not None)
                check("the other page never receives the private question",not any(f["type"]=="nutrition.question" for f in seen[id(b)]))
                mismatched=await answer(a,"bad-digest",q,digest="wrong")
                check("the initiating page cannot change the reviewed digest",not mismatched["data"]["accepted"])
                await a.send_json({"type":"nutrition.answer","request_id":"missing","operation":"bulk_apply","digest":q["digest"],"approve":True})
                await asyncio.sleep(.01)
                check("an ID-less private answer leaves the question unanswered",broker._scope is not None)
                # B's earlier answer consumed no question frame; an independent read also
                # proves its connection was not stalled by A's pending confirmation.
                check("another page can still read while a private question waits",(await call(b,"read-b","day",{"day":"2026-09-13"}))["ok"])
                approval=await answer(a,"approve",q);result=await receive(a,"nutrition.result","bulk")
                check("only the exact page answer commits the reviewed bulk change",approval["data"]["accepted"] and result["ok"] and result["data"]["strict"])
                retry=await call(a,"bulk","bulk_apply",scope)
                check("retry after a lost bulk receipt returns the same operation",retry["data"]==result["data"])
                check("a completed approval cannot be replayed",not (await answer(a,"replay-answer",q))["data"]["accepted"])
                await send(a,"bulk-undo","undo",{"operation_id":result["data"]["operation_id"]});undo_q=(await receive(a,"nutrition.question"))["data"]
                check("even a same-page latest bulk Undo asks a new scoped question",undo_q["operation"]=="bulk_undo" and undo_q["confirm_id"]!=q["confirm_id"])
                await answer(a,"undo-yes",undo_q);undone=await receive(a,"nutrition.result","bulk-undo")
                check("scoped Undo restores the saved meal's previous allowance",undone["ok"] and (await call(a,"after-undo","day",{"day":"2026-09-13"}))["data"]["totals"]["calories"]==220)
                scope=await review(a,"review-disconnect");await send(a,"disconnect-bulk","bulk_apply",scope);lost=(await receive(a,"nutrition.question"))["data"]
                await a.close()
                for _ in range(100):
                    if broker._scope is None:break
                    await asyncio.sleep(.005)
                check("disconnect denies its outstanding approval promptly",broker._scope is None)
                a=await connect(session)
                check("reconnecting with the same client label cannot revive a question",not (await answer(a,"reconnect-answer",lost))["data"]["accepted"])
                check("an old preview cannot be applied from the new socket session",not (await call(a,"old-preview","bulk_apply",scope))["ok"])
                frames=[json.loads(entry[3]) for entry in link._ring._frames]
                check("nutrition approvals are absent from replay types",all(not f.get("type","").startswith("nutrition.") for f in frames))
                scope=await review(a,"review-expire");await send(a,"expired-bulk","bulk_apply",scope);await receive(a,"nutrition.question")
                expired=await receive(a,"nutrition.result","expired-bulk")
                check("a page that never answers reaches the broker deadline without changes",not expired["ok"] and (await c.view(context(cfg),"2026-09-13"))["totals"]["calories"]==220)
                await a.close();await b.close()
        finally:
            await link.close();await c.close()
    print(f"\nall {len(CHECKS)} checks passed")


if __name__=="__main__":asyncio.run(main())
