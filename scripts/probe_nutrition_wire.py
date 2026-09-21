"""Probe nutrition through the real SDK dispatcher and authenticated sockets.

Temporary state and fake providers keep every personal path out of this probe.
The SDK dispatches independently of the caller, so live turn authority, generic
gate exclusion, and receipt delivery are exercised across that boundary.
Journal-only, nutrition-only, both, and neither configurations pin tool and
prompt visibility. Two real loopback sockets pin private responses, token and
Origin admission, reconnect retries, and revocation; no external network is used.
Library writes ask through the controller, exact defaults emit receipts without
another question, hypothetical reads leave intake alone, and bulk review directs
the owner to the page. Missing USDA credentials reach the model as a setup
problem; an estimated fallback names its source and allowance in the broker
question, saves nothing on refusal, and keeps unknown macros unknown on approval.
Nutrition history never gets a duplicate JSONL entry.
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from claude_agent_sdk import create_sdk_mcp_server
from claude_agent_sdk._internal.query import Query

from ciel import wire
from ciel.brain.agent import Brain
from ciel.brain.tools import build_tool_server
from ciel.brain.tools import actions
from ciel.brain.tools.nutrition import NUTRITION_TOOLS, bind_nutrition, history
from ciel.config import Config
from ciel.journal import ActionJournal
from ciel.nutrition import NutritionController, OwnerContext
from ciel.remote.web import WebLink
from ciel.task_context import TaskBinding
from ciel.turn import owner_origin

from probe_nutrition import meal, context
from probe_nutrition_photos import PNG, JPEG, CAPTURE
from ciel.turn import Attachment
from probe_task_tools import DispatcherClient, MemoryTransport, consume, response

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


class Client(DispatcherClient):
    def __init__(self) -> None:
        self.transport = MemoryTransport()
        server = create_sdk_mcp_server("ciel",tools=NUTRITION_TOOLS)
        self.dispatcher = Query(self.transport,is_streaming_mode=True,sdk_mcp_servers={"ciel":server["instance"]})
        self.calls=0
        self.name="nutrition_day"
        self.args: dict[str,Any] = {}

    async def query(self,text: str) -> None:
        self.calls+=1
        self.transport.expected=1
        await self.send_call(f"nutrition-call-{self.calls}",self.name,self.args)


async def sdk_checks(cfg: Config,controller: NutritionController) -> None:
    from ciel.brain.permissions import WorkspaceGuard, forbidden_names
    from ciel.brain.shellguard import classify
    root=cfg.state_dir
    for journal_on,nutrition_on in ((True,False),(False,True),(True,True),(False,False)):
        selected=replace(cfg,journal=replace(cfg.journal,enabled=journal_on),nutrition=replace(cfg.nutrition,enabled=nutrition_on))
        servers,allowed,_,_,journal,_=build_tool_server(selected)
        brain=Brain(selected,journal=journal)
        prompt=brain._build_options(servers,allowed).system_prompt
        check(f"journal={journal_on}, nutrition={nutrition_on}: reader visibility follows either source",
              ("mcp__ciel__recent_actions" in allowed)==(journal_on or nutrition_on))
        check(f"journal={journal_on}, nutrition={nutrition_on}: undo guidance names exactly the enabled sources",
              ("# The private food log" in prompt)==nutrition_on and ("Restore an edited file" in prompt)==journal_on)
        check(f"journal={journal_on}, nutrition={nutrition_on}: nutrition tools are private and opt-in",
              all((f"mcp__ciel__{t.name}" in allowed)==nutrition_on for t in NUTRITION_TOOLS))
    journal=ActionJournal(cfg.journal);journal.ensure()
    journal.record(tool="fixture_file_action",args={"path":"example.txt"},response="Recorded file fixture")
    verify,questions,receipts=[],[],[]
    async def ask(text: str) -> bool:
        questions.append(text);return True
    async def emit(ctx: OwnerContext,value: dict[str,Any]) -> None:
        receipts.append(value)
    controller.ask,controller.emit=ask,emit
    brain=Brain(cfg,journal=journal,confirmer=ask,verify_emitter=lambda *args:verify.append(args))
    client=Client();await client.start();brain._client=client
    admitted_files: list[Attachment] = []
    def current() -> OwnerContext | None:
        binding=brain._task_authority.capture()
        return OwnerContext(binding,"sdk-session",attachments=tuple(admitted_files)) if binding is not None else None
    bind_nutrition(controller,current)
    actions.bind_journal(journal);actions.bind_nutrition_history(history)
    names={f"mcp__ciel__{t.name}" for t in NUTRITION_TOOLS}
    check("nutrition never enters the generic gate, recorder watch list, or Vigil verification",
          not names.intersection(brain._gated_tools) and not names.intersection(brain._recorder._watched) and not names.intersection(brain._recorder._verify_for))
    try:
        owner=owner_origin(cfg.tasks.owner,"voice")
        client.args={"day":"2026-09-13"}
        await consume(brain,owner)
        check("the real SDK dispatcher reads a live private nutrition diary","diary_id" in response(client) and "isError" not in response(client))
        check("turn end revokes nutrition's tool authority",current() is None)
        await consume(brain,None)
        check("a dispatcher call without live owner authority cannot read nutrition","live private owner" in response(client))
        source=(await controller.view(context(cfg),"2026-09-13"))["meals"][0]
        client.name="nutrition_repeat";client.args={"source_id":source["id"],"occurred_at":"2026-09-13T14:00:00-07:00"}
        await consume(brain,owner_origin(cfg.tasks.owner,"voice"))
        check("SDK-dispatched exact repeats emit a receipt without generic confirmation or Vigil",len(receipts)==1 and not questions and not verify and "Logged" in receipts[0]["text"])
        check("the generic JSONL journal gets no copy of the nutrition mutation",len(journal.recent(50))==1)
        client.name="nutrition_undo";client.args={}
        await consume(brain,owner_origin(cfg.tasks.owner,"voice"))
        check("SDK-dispatched same-session Undo emits its reversal without a second yes",len(receipts)==2 and receipts[-1]["undo_of"]==receipts[0]["operation_id"] and not questions)
        client.name="nutrition_save";client.args=meal("SDK proposal")
        await consume(brain,owner_origin(cfg.tasks.owner,"voice"))
        check("SDK-dispatched proposed values reach the nutrition controller's broker",len(questions)==1 and "SDK proposal" in questions[0])
        brain._task_authority.install(owner_origin(cfg.tasks.owner,"voice"))
        try:
            merged=await actions.recent_actions.handler({"count":50})
            text=merged["content"][0]["text"]
            check("Inverse merges source-qualified nutrition and journal records with nutrition undo guidance",
                  "journal:" in text and "nutrition:" in text and "nutrition_undo" in text and "fixture_file_action" in text)
            check("newer nutrition history precedes the older journal record",text.index("SDK proposal")<text.index("fixture_file_action"))
            async def unavailable(count: int) -> list[dict[str,Any]]:
                raise RuntimeError("fixture unavailable")
            actions.bind_nutrition_history(unavailable)
            partial=await actions.recent_actions.handler({"count":50})
            check("an unavailable configured source is reported as partial, never silently empty","Partial action history" in partial["content"][0]["text"] and "fixture_file_action" in partial["content"][0]["text"])
        finally:
            await brain._task_authority.clear()
        photo_id="b"*32;photo_path=root/(photo_id+"-label.png");photo_path.write_bytes(PNG)
        admitted_files.append(Attachment("label.png","image/png",str(photo_path),len(PNG)))
        client.name="nutrition_photo_import";client.args={"attachment_id":photo_id,"purpose":"label"}
        await consume(brain,owner_origin(cfg.tasks.owner,"voice"))
        rows=(await controller.photos.listing(context(cfg)))["drafts"]
        check("SDK photo import uses only the attachment admitted with its owner turn",len(rows)==1 and rows[0]["value"]["capture"]["source"]=="chart" and photo_path.exists())
        client.name="nutrition_photo_analyze";client.args={"draft_id":rows[0]["id"],"revision":rows[0]["revision"],"notes":"Read this label."}
        before_calls=client.calls;before_questions=len(questions)
        await consume(brain,owner_origin(cfg.tasks.owner,"voice"))
        queued=(await controller.photos.listing(context(cfg)))["drafts"][0]
        check("SDK photo analysis saves a pending job with no recursive model or generic question",client.calls==before_calls+1 and len(questions)==before_questions and queued["value"]["job"] is not None and queued["value"]["job"]["task_id"] is None)
        admitted_files.clear()
        client.name="nutrition_photo_import";client.args={"attachment_id":photo_id}
        await consume(brain,owner_origin(cfg.tasks.owner,"voice"))
        check("a later SDK turn cannot reuse an attachment that was not admitted again","not admitted" in response(client))
        client.name="nutrition_catalog_save";client.args={"kind":"food","name":"SDK usual oats","revision":0,"items":meal()["items"]}
        before_questions=len(questions);await consume(brain,owner_origin(cfg.tasks.owner,"voice"))
        entry=(await controller.library.catalog(context(cfg),{"query":"SDK usual oats"}))["entries"][0]
        check("SDK saved-food creation asks through the controller and keeps a version",len(questions)==before_questions+1 and entry["revision"]==1)
        client.name="nutrition_log_saved";client.args={"source_kind":"food","source_id":entry["id"],"source_revision":1,"occurred_at":"2026-09-13T18:00:00-07:00"}
        before_questions=len(questions);before_receipts=len(receipts);await consume(brain,owner_origin(cfg.tasks.owner,"voice"))
        check("SDK exact-default logging bypasses generic questions but emits its receipt",len(questions)==before_questions and len(receipts)==before_receipts+1 and not verify)
        before_meals=(await controller.view(context(cfg),"2026-09-13"))["meals"]
        client.name="nutrition_preview";client.args={"source_kind":"food","source_id":entry["id"],"source_revision":1,"day":"2026-09-13"}
        await consume(brain,owner_origin(cfg.tasks.owner,"voice"))
        check("SDK hypothetical previews leave actual meals unchanged","projected" in response(client) and (await controller.view(context(cfg),"2026-09-13"))["meals"]==before_meals)
        client.name="nutrition_bulk_preview";client.args={"from":"2026-09-13","to":"2026-09-13"}
        await consume(brain,owner_origin(cfg.tasks.owner,"voice"))
        check("SDK bulk preview directs its human approval to the page","page_required" in response(client) and "preview_id" not in response(client))
        check("new library and planning tools never copy nutrition into JSONL",len(journal.recent(50))==1)
        client.name="nutrition_dashboard";client.args={"from":"2026-09-13","to":"2026-09-13"}
        await consume(brain,owner_origin(cfg.tasks.owner,"voice"))
        check("SDK trend questions use the shared dashboard aggregates","weekly_review" in response(client) and "cumulative_deficit" in response(client))
        before_questions=len(questions);client.name="nutrition_weight_save";client.args={"day":"2026-09-13","revision":0,"value":70,"unit":"kg"}
        await consume(brain,owner_origin(cfg.tasks.owner,"voice"))
        check("SDK manual weight uses controller review and a deterministic receipt",len(questions)==before_questions+1 and "70 kg" in receipts[-1]["text"])
        check("weight and dashboard tools stay out of the file journal",len(journal.recent(50))==1)
        before_meals=(await controller.view(context(cfg),"2026-09-13"))["meals"]
        before_questions=len(questions);before_receipts=len(receipts)
        client.name="nutrition_search";client.args={"query":"uncached fixture oats"}
        with patch("urllib.request.urlopen") as fetch:
            await consume(brain,owner_origin(cfg.tasks.owner,"voice"))
        check("SDK lookup reports a missing USDA key and a reviewed estimate fallback",
              "not configured" in response(client) and "clearly labeled estimate for review" in response(client) and not fetch.called)
        check("an unavailable lookup logs no meal and asks no mutation question",
              (await controller.view(context(cfg),"2026-09-13"))["meals"]==before_meals and len(questions)==before_questions and len(receipts)==before_receipts)
        proposal=meal("Estimated fallback")
        proposal["items"][0].update(source="estimate",protein_g=None,carbs_g=None,fat_g=None)
        client.name="nutrition_save";client.args=proposal
        async def refuse_estimate(text: str) -> bool:
            questions.append(text);return False
        controller.ask=refuse_estimate
        await consume(brain,owner_origin(cfg.tasks.owner,"voice"))
        check("the fallback review names estimated nutrition and the computed allowance",
              "estimated nutrition" in questions[-1] and "220 calories including 20 extra" in questions[-1])
        check("declining an estimated fallback preserves intake and emits no saved receipt",
              (await controller.view(context(cfg),"2026-09-13"))["meals"]==before_meals and len(receipts)==before_receipts and "not approved" in response(client))
        controller.ask=ask
        await consume(brain,owner_origin(cfg.tasks.owner,"voice"))
        estimated=next(m for m in (await controller.view(context(cfg),"2026-09-13"))["meals"] if m["name"]=="Estimated fallback")
        check("an approved fallback saves one labeled estimate and one visible allowance",
              len(questions)==before_questions+2 and len(receipts)==before_receipts+1 and estimated["items"][0]["source"]=="estimate" and estimated["totals"]["calories"]==220 and estimated["allowance"]==20)
        check("calories-only estimates preserve unknown macros and nutrition-only history",
              all(estimated["totals"][k] is None for k in ("protein_g","carbs_g","fat_g")) and len(journal.recent(50))==1)
        public=Brain(cfg,public=True)._build_options()
        check("public options contain no nutrition tools, providers, or undo guidance",public.mcp_servers=={} and "nutrition" not in public.system_prompt.lower())
        private_root=root/"custom-foods";private_root.mkdir()
        chosen=replace(cfg,nutrition=replace(cfg.nutrition,state_dir=private_root,fdc_api_key_file=root/"custom-food-key"),
                       files=replace(cfg.files,enabled=True,workspace=root))
        guard=WorkspaceGuard.from_config(chosen)
        for path in (private_root/"ordinary-name.json",root/"custom-food-key",root/"nutrition.sqlite3-wal"):
            check(f"{path.name} cannot bypass nutrition through file or shell tools",
                  guard.permits(str(path)) is not None and classify(f"cat {path}",chosen.shell,forbidden=forbidden_names(chosen))[0]=="deny")
    finally:
        bind_nutrition(None,lambda:None);actions.bind_nutrition_history(None);actions.bind_journal(None)
        await brain.close()


async def socket_checks(cfg: Config,controller: NutritionController) -> None:
    import aiohttp
    link=WebLink(replace(cfg.web,port=0),cfg.hub)
    link.bind_nutrition(controller)
    async def emit(ctx: OwnerContext,value: dict[str,Any]) -> None:
        link.nutrition_receipt(value)
    controller.emit=emit
    await link.start()
    port=link._site._server.sockets[0].getsockname()[1]
    base=f"http://127.0.0.1:{port}"
    async def result(ws: Any,identity: str) -> dict[str,Any]:
        while True:
            frame=await ws.receive_json(timeout=3)
            if frame.get("type")=="nutrition.result" and frame["request_id"]==identity:
                return frame
    async def admitted(session: Any) -> Any:
        ws=await session.ws_connect(base+"/ws")
        await ws.send_json({"type":"hello","v":1,"role":"chart","token":"fixture-token","client_id":"nutrition-probe"})
        check("the socket authenticates before returning its hello",(await ws.receive_json(timeout=3))["type"]=="hello")
        return ws
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(base+"/nutrition") as reply:
                shell=await reply.text()
                check("the nutrition GET is an empty self-contained shell",reply.status==200 and "SDK proposal" not in shell and "fixture-token" not in shell and "https://fonts" not in shell)
            for path in ("/nutrition.sqlite3","/nutrition/media/1","/nutrition/data"):
                async with session.get(base+path) as reply:
                    check(f"{path} exposes no data or media GET",reply.status==404)
            bad=await session.ws_connect(base+"/ws")
            await bad.send_json({"type":"hello","v":1,"role":"chart","token":""})
            refusal=await bad.receive_json(timeout=3)
            check("tokenless loopback sockets are refused before nutrition data",refusal["type"]=="error" and refusal["code"]==4401)
            await bad.close()
            try:
                await session.ws_connect(base+"/ws",headers={"Origin":"https://unrelated.invalid"})
            except aiohttp.WSServerHandshakeError as exc:
                check("another website's Origin cannot open nutrition's socket",exc.status==403)
            else:
                check("another website's Origin cannot open nutrition's socket",False)
            one,two=await admitted(session),await admitted(session)
            seq=link._ring.seq
            await one.send_json({"type":"nutrition.request","request_id":"read-one","operation":"day","data":{"day":"2026-09-13"}})
            view=await result(one,"read-one")
            check("the admitted page reads the shared controller's diary",view["ok"] and view["data"]["diary_id"]==controller.store.diary_id)
            try:
                await two.receive_json(timeout=.1)
            except asyncio.TimeoutError:
                check("a page read is replied to only on the requesting socket",True)
            else:
                check("a page read is replied to only on the requesting socket",False)
            await one.send_json({"type":"nutrition.request","request_id":"dashboard-one","operation":"dashboard","data":{"from":"2026-09-13","to":"2026-09-13"}})
            dashboard=await result(one,"dashboard-one")
            shared=await controller.dashboard.read(context(cfg),{"from":"2026-09-13","to":"2026-09-13"})
            check("page and voice use identical dashboard and weekly values",dashboard["ok"] and dashboard["data"]==shared)
            try:
                await two.receive_json(timeout=.1)
            except asyncio.TimeoutError:
                check("a dashboard and weight reading stays on its requesting socket",True)
            else:
                check("a dashboard and weight reading stays on its requesting socket",False)
            for image_bytes,suffix in ((PNG,"png"),(JPEG,"jpeg")):
                upload={"type":"nutrition.upload","request_id":"photo-"+suffix,"capture":CAPTURE,"data":base64.b64encode(image_bytes).decode()}
                await one.send_json(upload);photo=await result(one,"photo-"+suffix)
                check(f"authenticated {suffix} upload returns a durable private draft",photo["ok"] and photo["data"]["draft"]["value"]["state"]=="received")
                await one.send_json(upload);retry=await result(one,"photo-"+suffix)
                check(f"a retried {suffix} upload returns the same draft",retry["data"]==photo["data"])
                await one.send_json({"type":"nutrition.request","request_id":"media-"+suffix,"operation":"media","data":{"media_id":photo["data"]["draft"]["id"]}})
                media=await result(one,"media-"+suffix)
                check(f"private {suffix} reads preserve bytes and MIME",media["ok"] and base64.b64decode(media["data"]["data"])==image_bytes and media["data"]["mime"]==("image/png" if suffix=="png" else "image/jpeg"))
            try:
                await two.receive_json(timeout=.1)
            except asyncio.TimeoutError:
                check("photo bytes and draft replies never reach another socket or replay",link._ring.seq==seq)
            else:
                check("photo bytes and draft replies never reach another socket or replay",False)
            await one.send_json({"type":"nutrition.upload","request_id":"bad-image","capture":CAPTURE,"data":base64.b64encode(b"GIF89a").decode()})
            invalid=await result(one,"bad-image")
            check("unsupported image formats fail without a draft receipt",not invalid["ok"])
            await one.send_json({"type":"nutrition.request","request_id":"weight-page","operation":"weight_save","data":{"day":"2026-09-12","revision":0,"value":155,"unit":"lb"}})
            weight=await result(one,"weight-page")
            check("the authenticated page can save a reviewed manual measurement",weight["ok"] and "155 lb" in weight["data"]["text"])
            frame={"type":"nutrition.request","request_id":"page-save-once","operation":"save","data":meal("Socket fixture")}
            await one.send_json(frame)
            saved=await result(one,"page-save-once")
            check("a reviewed socket Save returns its committed receipt",saved["ok"] and "Socket fixture" in saved["data"]["text"])
            check("nutrition data and receipts never enter the shared replay ring",link._ring.seq==seq and not any(t.startswith("nutrition.") for t in wire.BROADCAST_TYPES))
            await one.close()
            again=await admitted(session)
            await again.send_json(frame)
            retried=await result(again,"page-save-once")
            check("reconnect retry reuses the durable receipt without another meal",retried["data"]==saved["data"])
            state=await controller.view(context(cfg),"2026-09-13")
            check("the retried page save exists exactly once",sum(m["name"]=="Socket fixture" for m in state["meals"])==1)
            await again.send_json({"type":"nutrition.upload","request_id":"photo-png","capture":CAPTURE,"data":base64.b64encode(PNG).decode()})
            retried_photo=await result(again,"photo-png")
            check("an upload retry after socket reconnect still names one durable draft",retried_photo["ok"] and retried_photo["data"]["draft"]["revision"]==1)
            bindings=list(link._nutrition_bindings.values())
            await again.close();await two.close()
            for _ in range(100):
                if not link._nutrition_bindings:break
                await asyncio.sleep(.01)
            check("disconnect revokes every nutrition socket binding",all(not b._lease.active for b in bindings))
            for bad_frame in ({**frame,"request_id":""},{**frame,"operation":"bulk_recalculate"},{**frame,"data":{"oversized":"x"*70000}}):
                try: wire.validate(bad_frame,"c2h")
                except wire.WireError: denied=True
                else: denied=False
                check("malformed or out-of-scope nutrition requests fail the wire catalog",denied)
    finally:
        await link.close()


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ciel-nutrition-wire-") as tmp,patch.object(Path,"home",return_value=Path(tmp)):
        root=Path(tmp);cfg=Config()
        cfg=replace(cfg,state_dir=root,timezone="America/Los_Angeles",
                    hub=replace(cfg.hub,require_token=True,token="fixture-token",token_file=root/"missing-token"),
                    nutrition=replace(cfg.nutrition,enabled=True,owner_host="fixture-host",state_dir=root/"nutrition-state"))
        c=NutritionController(cfg,host="fixture-host")
        await c.start()
        try:
            await c.apply(context(cfg),"save",meal())
            await sdk_checks(cfg,c)
            await socket_checks(cfg,c)
        finally:
            await c.close()
    print(f"\nall {len(CHECKS)} checks passed")


if __name__=="__main__":
    asyncio.run(main())
