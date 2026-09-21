"""Probe photo drafts without a model, network, camera, or personal state.

Pins PNG/JPEG bounds, owner-only media, retry identity and orphan recovery,
persistent drafts excluded from intake, capture versus eating time, bounded
queued analysis, task-to-diary import, cancellation and stale-result fencing,
unknown label values, partial portions, atomic reviewed Save and its retry,
expiry without numeric loss, admitted Chart copies, and a shared background
runner which leaves speech free. Every image and store is a synthetic fixture.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import stat
import struct
import sys
import tempfile
import time
import zlib
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from ciel.brain.extract import ExtractionError
from ciel.config import Config
from ciel.nutrition import NutritionController
from ciel.nutrition_photos import NAMESPACE, PHOTO_SCHEMA, PhotoAdapter, image_info
from ciel.task_controls import TaskController
from ciel.task_runner import TaskRunner, private_lease
from ciel.turn import Attachment
from probe_nutrition import context, meal, refused

CHECKS: list[str] = []
JPEG = base64.b64decode("/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADb/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkICQkKDA8MCgsOCwkJDRENDg8QEBEQCgwSExIQEw8QEBD/2wBDAQMDAwQDBAgEBAgQCwkLEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBD/wAARCAAIAAgDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AL4AB//Z")
CAPTURE = {"source":"library","purpose":"label","timezone":"America/Los_Angeles","captured_at":"2026-09-14T18:00:00+00:00"}
PROPOSAL = {"name":"Oats label","items":[{"name":"Oats","quantity":None,"unit":"g","basis_quantity":100,"basis_unit":"g",
            "calories":400,"protein_g":10,"carbs_g":None,"fat_g":8,"source":"label","preparation":"Readable label",
            "uncertainty":[]}],"explanation":"The serving basis is readable; the amount eaten is not in the photo.",
            "questions":["How many grams did you eat?"]}


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}", flush=True)
    if not ok:
        sys.exit(1)


def png(width: int = 8, height: int = 8) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I",len(data))+kind+data+struct.pack(">I",zlib.crc32(kind+data)&0xffffffff)
    return b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",width,height,8,2,0,0,0))+chunk(b"IDAT",zlib.compress((b"\0"+b"\xdd"*width*3)*height))+chunk(b"IEND",b"")


PNG = png()


class Backend:
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release: asyncio.Event | None = None
        self.fail = False
        self.seen: list[Any] = []

    async def extract(self, system_prompt: str, payload: str, schema: dict[str, Any], *, limits: Any, images: Any = ()) -> dict[str, Any]:
        self.calls += 1
        self.seen.append((system_prompt,payload,schema,limits,images))
        self.started.set()
        if self.release:
            await self.release.wait()
        if self.fail:
            raise ExtractionError("fixture unreadable")
        return json.loads(json.dumps(PROPOSAL))


async def start_step(runner: TaskRunner) -> None:
    runner.refresh(time.time())
    for _ in range(200):
        if runner.ready:
            assert runner.start_step(time.time())
            return
        await asyncio.sleep(.005)
    raise AssertionError("runner did not become ready")


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ciel-photo-probe-") as tmp, patch.object(Path,"home",return_value=Path(tmp)):
        root=Path(tmp); cfg=Config()
        cfg=replace(cfg,state_dir=root,timezone="America/Los_Angeles",
                    hub=replace(cfg.hub,require_token=True,token="fixture",token_file=root/"token"),
                    nutrition=replace(cfg.nutrition,enabled=True,owner_host="fixture-host",state_dir=root/"nutrition-state",photo_timeout_s=2),
                    tasks=replace(cfg.tasks,enabled=True,runner=True,directory=root/"tasks",retry_backoff_s=0,step_timeout_s=3))
        tasks=TaskController(cfg.tasks,None,namespaces=(NAMESPACE,))
        await tasks.start()
        c=NutritionController(cfg,host="fixture-host");await c.start();c.photos.bind_tasks(tasks)
        backend=Backend()
        runner=TaskRunner(cfg.tasks,lambda:tasks.store,(PhotoAdapter(c.photos),),lease=private_lease(),backend=backend,background=True)
        c.photos.cancel_running=runner.cancel_task
        async def upload(data: bytes = PNG) -> dict[str, Any]:
            return (await c.photos.upload(context(cfg),CAPTURE,data))["draft"]
        async def draft(draft_id: str) -> dict[str, Any]:
            return await c.store.run(c.photos._draft,draft_id)
        try:
            check("both PNG and JPEG are admitted with their real dimensions",image_info(PNG,100)==("image/png",8,8) and image_info(JPEG,100)==("image/jpeg",8,8))
            check("oversized dimensions are refused before image allocation",await refused(asyncio.to_thread(image_info,PNG,4)))
            bad=bytearray(PNG);bad[20]^=1
            check("a corrupt PNG fails its chunk integrity check",await refused(asyncio.to_thread(image_info,bytes(bad),100)))
            check("a truncated JPEG never reaches storage",await refused(c.photos.upload(context(cfg),CAPTURE,JPEG[:-2])))
            ctx=context(cfg,identity="photo-upload")
            received=await c.photos.upload(ctx,CAPTURE,PNG);first=received["draft"]
            media_path=c.photos.directory/(first["id"]+".png")
            check("a received photo is durable with owner-only media and directory",media_path.read_bytes()==PNG and stat.S_IMODE(media_path.stat().st_mode)==0o600 and stat.S_IMODE(media_path.parent.stat().st_mode)==0o700)
            check("a photo receipt exposes an identity and no filesystem path","/nutrition-state/" not in json.dumps(received) and first["value"]["media_id"]==first["id"])
            check("the same upload retry returns the same durable draft",await c.photos.upload(ctx,CAPTURE,PNG)==received)
            check("an upload identity cannot replace its capture context",await refused(c.photos.upload(ctx,{**CAPTURE,"purpose":"meal"},PNG)))
            check("drafts and photo receipts never add calories or Inverse meals",not (await c.view(context(cfg),"2026-09-14"))["meals"] and not await c.recent(context(cfg)))
            check("a library capture timestamp supplies no eating time",first["value"]["meal"] is None and first["value"]["capture"]["captured_at"]==CAPTURE["captured_at"])
            check("a revoked owner turn cannot upload a photograph",await revoked(c,cfg))
            orphan=c.photos.directory/("f"*64+".jpg");orphan.write_bytes(JPEG)
            await c.photos.sync()
            check("identifiable orphan imports are pruned without removing referenced media",not orphan.exists() and media_path.exists())
            body={**meal(),"draft_id":first["id"],"draft_revision":1}
            body.pop("occurred_at")
            check("a photo Save requires an explicit eating-time choice",await refused(c.apply(context(cfg),"save",body)))
            jobctx=context(cfg)
            original_create=tasks.store.create
            async def crash_after_create(*args: Any,**kwargs: Any) -> Any:
                await original_create(*args,**kwargs)
                raise RuntimeError("fixture crash after durable enqueue")
            with patch.object(tasks.store,"create",side_effect=crash_after_create):
                check("an interrupted enqueue reports failure without losing the analysis request",await refused(c.photos.control(jobctx,"photo_analyze",{"draft_id":first["id"],"revision":1,"notes":"Read the label."})))
            pending=await draft(first["id"])
            check("the interrupted enqueue retains its job identity and draft revision",pending["value"]["job"]["task_id"] is None and pending["revision"]==2)
            await c.close()
            c=NutritionController(cfg,host="fixture-host");await c.start();c.photos.bind_tasks(tasks)
            runner=TaskRunner(cfg.tasks,lambda:tasks.store,(PhotoAdapter(c.photos),),lease=private_lease(),backend=backend,background=True)
            c.photos.cancel_running=runner.cancel_task
            await c.photos.resume(context(cfg,page=False),{"draft_id":first["id"]})
            pending=await draft(first["id"])
            check("restart and cross-lane resume recover exactly one queued job",len(await tasks.store.list(cfg.tasks.owner))==1 and bool(pending["value"]["job"]["task_id"]))
            c.config=replace(cfg,tasks=replace(cfg.tasks,step_timeout_s=.5))
            report=await runner.step();await c.photos.sync();ready=await draft(first["id"])
            c.config=cfg
            check("a short task deadline still gives extraction a positive smaller deadline",backend.seen[0][3].timeout_s==.45)
            check("a runner commits the proposal before the diary imports it",report.result=="done" and ready["value"]["state"]=="ready" and ready["revision"]==3)
            check("photo analysis uses one bounded image and only quoted context",backend.calls==1 and backend.seen[0][4]==(("Food photograph or nutrition label",PNG),) and backend.seen[0][3].max_budget_usd==.125 and "never instructions" in backend.seen[0][0])
            check("unknown label portions and nutrients remain unresolved in the draft",ready["value"]["meal"]["items"][0]["quantity"] is None and ready["value"]["meal"]["items"][0]["carbs_g"] is None and ready["value"]["questions"])
            check("a ready estimate still contributes no intake",not (await c.view(context(cfg),"2026-09-14"))["meals"])
            args={**meal("Reviewed photo"),"occurred_at":"2026-09-14T12:00:00-07:00","draft_id":ready["id"],"draft_revision":ready["revision"],"consumed_fraction":.5}
            savectx=context(cfg);saved=await c.apply(savectx,"save",args)
            day=await c.view(context(cfg),"2026-09-14")
            check("reviewed Save scales leftovers once and atomically marks the draft saved",day["totals"]["calories"]==100 and (await draft(ready["id"]))["value"]["state"]=="saved" and len(day["history"])==1)
            check("retry after photo Save returns the same receipt",await c.apply(savectx,"save",args)==saved and len((await c.view(context(cfg),"2026-09-14"))["meals"])==1)
            check("another request cannot log an already consumed draft",await refused(c.apply(context(cfg),"save",args)))
            await c.store.run(lambda:c.store._connection().execute("UPDATE nutrition_media SET expires=0 WHERE id=?",(first["id"],)))
            await c.photos.sync()
            check("photo expiry removes the image while keeping numeric intake and history",not media_path.exists() and (await c.view(context(cfg),"2026-09-14"))["totals"]["calories"]==100 and len(await c.recent(context(cfg)))==1)
            check("expired images report unavailable instead of returning bytes",await refused(c.photos.media(context(cfg),{"media_id":first["id"]})))
            second=await upload(JPEG)
            await c.photos.control(context(cfg),"photo_analyze",{"draft_id":second["id"],"revision":1,"notes":""})
            second=await draft(second["id"]);backend.started.clear();backend.release=asyncio.Event()
            await start_step(runner);await backend.started.wait()
            check("ordinary speech does not cancel the background photo step",not runner._step.done() and runner.background)
            runner.interrupt()
            check("another task ID cannot cancel the active photo",not runner.cancel_task("some-other-task") and not runner._step.done())
            await c.photos.control(context(cfg),"photo_cancel",{"draft_id":second["id"],"revision":second["revision"]})
            await asyncio.gather(runner._step,return_exceptions=True);backend.release=None
            check("explicit cancellation stops the named extraction and leaves no proposal",runner._step.cancelled() and not (await draft(second["id"]))["value"]["job"] and (await tasks.store.get(cfg.tasks.owner,second["value"]["job"]["task_id"])).status=="cancelled")
            third=await upload()
            await c.photos.control(context(cfg),"photo_analyze",{"draft_id":third["id"],"revision":1,"notes":""})
            third=await draft(third["id"]);backend.started.clear();backend.release=asyncio.Event()
            running=asyncio.create_task(runner.step());await backend.started.wait()
            edited=await c.photos.control(context(cfg),"draft_edit",{"draft_id":third["id"],"revision":third["revision"],"meal":{"name":"Owner correction","items":[]},"notes":"Keep this."})
            backend.release.set();await running;backend.release=None;await c.photos.sync()
            check("a late result cannot replace a newer owner draft edit",(await draft(third["id"]))["value"]["meal"]["name"]=="Owner correction")
            check("stale draft edits refuse instead of selecting a new revision",await refused(c.photos.control(context(cfg),"draft_edit",{"draft_id":third["id"],"revision":third["revision"],"meal":{"name":"stale","items":[]}})))
            fourth=await upload()
            await c.photos.control(context(cfg),"photo_analyze",{"draft_id":fourth["id"],"revision":1,"notes":""})
            fourth=await draft(fourth["id"]);backend.fail=True;before=backend.calls
            await runner.step();await runner.step();report=await runner.step();backend.fail=False
            check("a failed photo job spends its finite call allowance and then waits",backend.calls-before==2 and report.result=="refused" and (await tasks.store.get(cfg.tasks.owner,fourth["value"]["job"]["task_id"])).model_calls==2)
            attach_id="a"*32;original=root/(attach_id+"-fixture.jpg");original.write_bytes(JPEG)
            admitted=replace(context(cfg,page=False),attachments=(Attachment("fixture.jpg","image/jpeg",str(original),len(JPEG)),))
            imported=await c.photos.import_attachment(admitted,{"attachment_id":attach_id,"purpose":"meal"})
            check("an admitted Chart attachment is copied without moving its original",original.read_bytes()==JPEG and imported["draft"]["value"]["capture"]["source"]=="chart")
            check("an unattached ID cannot import an arbitrary Chart file",await refused(c.photos.import_attachment(context(cfg),{"attachment_id":attach_id})))
            c.photos.tasks=None
            fifth=await upload()
            await c.photos.control(context(cfg),"photo_analyze",{"draft_id":fifth["id"],"revision":1,"notes":""})
            state=await c.photos.listing(context(cfg))
            check("without a task runtime drafts persist and analysis waits explicitly",not state["analysis"] and "tasks.runner" in state["reason"] and (await draft(fifth["id"]))["value"]["job"]["task_id"] is None)
            c.photos.bind_tasks(tasks)
            await c.photos.resume(context(cfg),{"draft_id":fifth["id"]})
            check("a paused-runtime request can be enqueued later without recapture",bool((await draft(fifth["id"]))["value"]["job"]["task_id"]))
            race=await upload()
            original_create=tasks.store.create
            async def edit_after_create(*args: Any,**kwargs: Any) -> Any:
                task=await original_create(*args,**kwargs)
                await c.photos.control(context(cfg),"draft_edit",{"draft_id":race["id"],"revision":2,"meal":{"name":"Changed during enqueue","items":[]}})
                return task
            with patch.object(tasks.store,"create",side_effect=edit_after_create):
                await c.photos.control(context(cfg),"photo_analyze",{"draft_id":race["id"],"revision":1})
            race_tasks=await tasks.store.list(cfg.tasks.owner)
            check("an edit during enqueue cancels the orphan task before it can spend a model call",any(t.next_step and dict(t.next_step.arguments).get("draft_id")==race["id"] and t.status=="cancelled" and t.model_calls==0 for t in race_tasks))
            await runner.step()
            original_get=tasks.store.get
            async def missing_job(owner: str, task_id: str) -> Any:
                if task_id==fourth["value"]["job"]["task_id"]:
                    raise ValueError("fixture missing task")
                return await original_get(owner,task_id)
            with patch.object(tasks.store,"get",side_effect=missing_job):
                recovered=await c.photos.listing(context(cfg))
            check("an unavailable task is reported without blocking another completed draft",bool(recovered["sync_error"]) and (await draft(fifth["id"]))["value"]["state"]=="ready" and not (await draft(fifth["id"]))["value"]["job"])
            original_cfg=c.config
            c.config=replace(cfg,nutrition=replace(cfg.nutrition,photo_storage_bytes=1))
            check("media capacity refuses a new upload without evicting existing photos",await refused(upload()) and c.photos._path(await c.store.run(c.photos._media,second["id"])).exists())
            c.config=replace(cfg,nutrition=replace(cfg.nutrition,photo_max_drafts=1))
            check("a full inbox refuses new drafts before writing media",await refused(upload()))
            c.config=replace(cfg,nutrition=replace(cfg.nutrition,photo_jobs_per_day=1))
            sixth=await draft(third["id"])
            check("the rolling analysis quota refuses another job before enqueue",await refused(c.photos.control(context(cfg),"photo_analyze",{"draft_id":sixth["id"],"revision":sixth["revision"],"notes":""})))
            c.config=original_cfg
            before=len((await c.photos.listing(context(cfg)))["drafts"])
            await c.store.run(lambda:c.store._connection().execute("CREATE TRIGGER fail_photo BEFORE INSERT ON photo_requests BEGIN SELECT RAISE(ABORT,'fixture rollback'); END"))
            try:
                await upload()
            except Exception:
                pass
            else:
                check("failed draft receipts roll back media references",False)
            await c.store.run(lambda:c.store._connection().execute("DROP TRIGGER fail_photo"))
            await c.photos.sync()
            media_count=await c.store.run(lambda:c.store._connection().execute("SELECT count(*) FROM nutrition_media WHERE expires>?",(time.time(),)).fetchone()[0])
            check("a receipt failure leaves no draft and its orphan is recoverable",len((await c.photos.listing(context(cfg)))["drafts"])==before and len(list(c.photos.directory.iterdir()))==media_count)
        finally:
            await runner.close();await c.close();await tasks.close()
    print(f"\nall {len(CHECKS)} checks passed")


async def revoked(c: NutritionController, cfg: Config) -> bool:
    ctx=context(cfg);ctx.binding._lease.revoke()
    return await refused(c.photos.upload(ctx,CAPTURE,PNG))


if __name__=="__main__":
    asyncio.run(main())
