"""Probe familiar meals, separate planning, and scoped historical changes.

Temporary diaries pin source and policy snapshots, aliases and ambiguity,
recipe yield conversions, batch stability, unchanged old meals, explicit
questions, exact-default shortcuts, read-only previews, projected coverage,
atomic plan consumption and grouped Undo, retries, stale sources and scopes,
all-or-nothing recalculation, expiry, confirmations-off, and history limits.
No model, personal state, source API, or production configuration is used.
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

from ciel.config import Config
from ciel.nutrition import NutritionController, packed
from probe_nutrition import context, meal, refused

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}",flush=True)
    if not ok:sys.exit(1)


def fixture(root: Path) -> Config:
    cfg=Config()
    return replace(cfg,state_dir=root,timezone="America/Los_Angeles",
        hub=replace(cfg.hub,require_token=True,token="fixture-token",token_file=root/"missing-token"),
        nutrition=replace(cfg.nutrition,enabled=True,owner_host="fixture-host",state_dir=root/"nutrition-state",fdc_api_key_file=root/"missing-key"))


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ciel-library-probe-") as tmp,patch.object(Path,"home",return_value=Path(tmp)):
        cfg=fixture(Path(tmp));questions=[];receipts=[];scopes=[]
        async def ask(text: str) -> bool:questions.append(text);return True
        async def emit(ctx: Any,value: Any) -> None:receipts.append(value)
        c=NutritionController(cfg,host="fixture-host",ask=ask,emit=emit);await c.start();lib=c.library
        await c.store.run(lambda:c.store._connection().execute("PRAGMA user_version=2"))
        await c.close();await c.start()
        check("an existing photo diary upgrades without downgrading grouped-history support",await c.store.run(lambda:c.store._connection().execute("PRAGMA user_version").fetchone()[0])==3)
        async def approve(ctx: Any,operation: str,digest: str,report: Any,expires: float) -> bool:
            scopes.append((ctx.session,operation,digest,report));return True
        lib.ask_scoped=approve
        async def read(kind: str,identity: str) -> Any:return await c._read(context(cfg),c.store._record,kind,identity)
        async def write(op: str,args: dict[str,Any],ctx: Any=None) -> Any:return await lib.apply(ctx or context(cfg),op,args)
        food={"kind":"food","name":"Usual oats","aliases":["usual breakfast"],"revision":0,"items":meal()["items"]}
        try:
            saved=await write("catalog_save",food);food_id=saved["record_id"]
            check("a reviewed saved food has history and a receipt but no intake",len(await c.recent(context(cfg)))==1 and (await c.view(context(cfg),"2026-09-13"))["totals"]["calories"]==0 and saved["text"].startswith("Saved food"))
            check("a name or alias finds the same versioned food",(await lib.catalog(context(cfg),{"query":"USUAL BREAKFAST"}))["entries"][0]["revision"]==1)
            ctx=context(cfg,identity="retry-food");again=await write("catalog_save",{**food,"name":"Another oats"},ctx)
            check("retrying a library Save retains its identity",await write("catalog_save",{**food,"name":"Another oats"},ctx)==again)
            check("a request ID cannot name a different saved food",await refused(write("catalog_save",{**food,"name":"Conflict"},ctx)))
            await write("catalog_save",food)
            check("ambiguous aliases return candidates rather than selecting a meal",len((await lib.catalog(context(cfg),{"query":"usual breakfast"}))["entries"])==3)
            source={"source_kind":"food","source_id":food_id,"source_revision":1}
            logged=await write("catalog_log",{**source,"occurred_at":"2026-09-13T09:00:00-07:00"},context(cfg,page=False))
            check("an exact default logs without another question and emits its receipt",not questions and receipts[-1]==logged and (await c.view(context(cfg),"2026-09-13"))["totals"]["calories"]==200)
            await write("catalog_log",{**source,"fraction":.5,"occurred_at":"2026-09-13T10:00:00-07:00"},context(cfg,page=False))
            check("a changed default portion asks exactly once",len(questions)==1)
            old_meal=(await read("meal",logged["record_id"]))["value"]
            edited=await write("catalog_save",{**food,"id":food_id,"revision":1,"items":[{**meal()["items"][0],"calories":500}]},context(cfg,page=False))
            check("a voice default edit asks and old meals retain their snapshots",len(questions)==2 and (await read("meal",logged["record_id"]))["value"]==old_meal)
            check("a stale default cannot log whatever its new version became",await refused(write("catalog_log",source)))
            recipe={"kind":"recipe","name":"Four oat portions","revision":0,"aliases":[],"yield_quantity":4,"yield_unit":"serving","items":[{**meal()["items"][0],"quantity":200,"preparation":"dry weight, cooked as a batch"}]}
            r=await write("catalog_save",recipe);rid=r["record_id"]
            portion={"source_kind":"recipe","source_id":rid,"source_revision":1}
            check("one of four portions uses the whole recipe yield",(await lib.portion(context(cfg),portion))["meal"]["totals"]["calories"]==200)
            check("half a serving is one eighth of a four-serving recipe",(await lib.portion(context(cfg),{**portion,"quantity":.5}))["meal"]["totals"]["calories"]==100)
            check("an explicit fraction means a fraction of the whole batch",(await lib.portion(context(cfg),{**portion,"fraction":.5}))["meal"]["totals"]["calories"]==400)
            check("zero portions and mixed fraction/quantity requests refuse",await refused(lib.portion(context(cfg),{**portion,"fraction":0})) and await refused(lib.portion(context(cfg),{**portion,"fraction":.5,"quantity":1})))
            check("serving-to-mass conversion never guesses density",await refused(lib.portion(context(cfg),{**portion,"quantity":100,"unit":"g"})))
            mass=await write("catalog_save",{**recipe,"name":"A kilogram batch","yield_quantity":1,"yield_unit":"kg"})
            check("a measured fraction converts compatible yield units",(await lib.portion(context(cfg),{"source_kind":"recipe","source_id":mass["record_id"],"source_revision":1,"quantity":250,"unit":"g"}))["meal"]["totals"]["calories"]==200)
            batch=await write("batch_create",{"recipe_id":rid,"recipe_revision":1,"name":"Sunday batch"})
            batch_before=await read("batch",batch["record_id"])
            await write("catalog_save",{**recipe,"id":rid,"revision":1,"items":[{**recipe["items"][0],"calories":800}]})
            check("editing a recipe leaves prepared batch values and yield unchanged",await read("batch",batch["record_id"])==batch_before)
            check("batch preparation retains raw/cooked assumptions",batch_before["value"]["snapshot"]["items"][0]["preparation"]=="dry weight, cooked as a batch")
            await write("catalog_delete",{"kind":"recipe","id":rid,"revision":2})
            check("a deleted recipe does not invalidate an existing batch",(await lib.portion(context(cfg),{"source_kind":"batch","source_id":batch["record_id"],"source_revision":1}))["meal"]["totals"]["calories"]==200)
            hist=len(await c.recent(context(cfg),50));before=(await c.view(context(cfg),"2026-09-13"))["totals"]
            hypothetical=await lib.preview(context(cfg),{**source,"source_revision":2,"day":"2026-09-13"})
            check("a hypothetical preview writes no plan, meal, or history",len(await c.recent(context(cfg),50))==hist and not (await lib.plans(context(cfg),{"day":"2026-09-13"}))["plans"] and hypothetical["projected"]["calories"]==before["calories"]+250)
            check("projected macros expose partial coverage",hypothetical["coverage"]["carbs_g"]["known_items"]==0 and hypothetical["coverage"]["carbs_g"]["total_items"]==3)
            empty_preview=await lib.preview(context(cfg),{**source,"source_revision":2,"day":"2026-09-14"})
            check("an empty day's zero never turns an unknown projected nutrient into zero",empty_preview["projected"]["carbs_g"] is None and empty_preview["coverage"]["carbs_g"]["total_items"]==1)
            plan=await write("plan_save",{**source,"source_revision":2,"day":"2026-09-13","revision":0})
            planned=await lib.plans(context(cfg),{"day":"2026-09-13"})
            check("saved plans have projected totals while actual intake stays fixed",planned["projected"]["calories"]==before["calories"]+250 and planned["logged"]==before)
            pid=plan["record_id"]
            check("a plan needs an actual eating-time choice",await refused(write("plan_log",{"plan_id":pid,"plan_revision":1})))
            planctx=context(cfg,identity="eat-plan")
            planargs={"plan_id":pid,"plan_revision":1,"occurred_at":"2026-09-13T12:00:00-07:00"}
            during,consumed=await asyncio.gather(lib.plans(context(cfg),{"day":"2026-09-13"}),write("plan_log",planargs,planctx))
            check("a concurrent plan consumption cannot double count a projected meal",during["projected"]["calories"]==550)
            check("logging a plan commits meal and consumed state together",not (await lib.plans(context(cfg),{"day":"2026-09-13"}))["plans"] and (await c.view(context(cfg),"2026-09-13"))["totals"]["calories"]==550)
            check("retrying plan consumption returns the same receipt",await write("plan_log",planargs,planctx)==consumed)
            check("another request cannot consume the same plan twice",await refused(write("plan_log",planargs)))
            await c.apply(context(cfg,page=False),"undo",{"operation_id":consumed["operation_id"]})
            check("immediate Undo restores both the plan and the previous intake",len((await lib.plans(context(cfg),{"day":"2026-09-13"}))["plans"])==1 and (await c.view(context(cfg),"2026-09-13"))["totals"]==before)
            check("grouped history is readable by its affected plan ID",len((await c.inspect_history(context(cfg),{"record_id":pid}))["history"])==3)
            check("an untrusted USDA payload cannot create a sourced default",await refused(write("catalog_save",{**food,"items":[{**meal()["items"][0],"source":"usda","source_id":"invented"}]})))
            await c.apply(context(cfg),"complete",{"day":"2026-09-13","revision":0,"complete":True})
            estimated=await c.apply(context(cfg),"save",{**meal("Estimated dinner"),"items":[{**meal()["items"][0],"source":"estimate","uncertainty":["oil"]}]})
            fixed=await write("catalog_save",{"kind":"food","name":"Usual estimated dinner","revision":0,"source_meal_id":estimated["record_id"],"source_meal_revision":1})
            original_cfg=c.config
            c.config=replace(cfg,nutrition=replace(cfg.nutrition,estimate_allowance_pct=20,oil_allowance_pct=20))
            fixed_portion=await lib.portion(context(cfg),{"source_kind":"food","source_id":fixed["record_id"],"source_revision":1,"fraction":.5})
            check("saved portions retain their source policy after configuration changes",fixed_portion["meal"]["totals"]["calories"]==120 and fixed_portion["meal"]["policy"]["percentages"]["oil"]==10)
            reviewctx=context(cfg)
            scope=await lib.bulk_preview(reviewctx,{"from":"2026-09-13","to":"2026-09-13"})
            check("bulk preview shows every named meal and its before/after calories",scope["count"]==3 and scope["after"]-scope["before"]==40 and len(scopes)==0)
            args={"preview_id":scope["preview_id"],"digest":scope["digest"]}
            check("another page cannot borrow a reviewed scope",await refused(write("bulk_apply",args,context(cfg,session="other-page"))))
            check("a different digest cannot approve a reviewed range",await refused(write("bulk_apply",{**args,"digest":"different"},reviewctx)))
            bulkctx=replace(reviewctx,binding=context(cfg,identity="bulk-apply").binding)
            bulk=await write("bulk_apply",args,bulkctx)
            check("a scoped bulk change preserves completion and dates",len(scopes)==1 and scopes[-1][1]=="bulk_apply" and (await c.view(context(cfg),"2026-09-13"))["completion"]["value"]["complete"] and (await read("meal",estimated["record_id"]))["value"]["occurred_at"]==meal()["occurred_at"])
            check("bulk retry returns its durable receipt after its preview is gone",await write("bulk_apply",args,bulkctx)==bulk and len(scopes)==1)
            check("voice Undo of even the latest bulk operation requires the page",await refused(c.apply(context(cfg,page=False),"undo",{"operation_id":bulk["operation_id"]})))
            await c.apply(context(cfg),"undo",{"operation_id":bulk["operation_id"]})
            check("bulk Undo requires its own scoped question and restores original allowances",len(scopes)==2 and scopes[-1][1]=="bulk_undo" and (await read("meal",estimated["record_id"]))["value"]["allowance"]==40)
            scope=await lib.bulk_preview(reviewctx,{"from":"2026-09-13","to":"2026-09-13"})
            existing=await read("meal",estimated["record_id"])
            await c.apply(context(cfg),"save",{**existing["value"],"id":existing["id"],"revision":existing["revision"],"name":"Owner edit"})
            before_rows=(await c.view(context(cfg),"2026-09-13"))["meals"]
            check("one stale meal rolls the entire bulk transaction back",await refused(write("bulk_apply",{"preview_id":scope["preview_id"],"digest":scope["digest"]},reviewctx)) and (await c.view(context(cfg),"2026-09-13"))["meals"]==before_rows)
            scope=await lib.bulk_preview(reviewctx,{"from":"2026-09-13","to":"2026-09-13"})
            lib.reviews[scope["preview_id"]]=replace(lib.reviews[scope["preview_id"]],expires=time.monotonic()-1)
            check("an expired review cannot open an approval",await refused(write("bulk_apply",{"preview_id":scope["preview_id"],"digest":scope["digest"]},reviewctx)))
            c.config=replace(c.config,confirm=replace(cfg.confirm,ask_first=False))
            check("confirmations-off refuses bulk preview before changes",await refused(lib.bulk_preview(reviewctx,{"from":"2026-09-13","to":"2026-09-13"})))
            c.config=original_cfg
            scope=await lib.bulk_preview(reviewctx,{"from":"2026-09-13","to":"2026-09-13"})
            async def disable_before_commit(*args: Any) -> bool:
                c.config=replace(c.config,confirm=replace(cfg.confirm,ask_first=False));return True
            lib.ask_scoped=disable_before_commit
            check("turning confirmations off after Yes still refuses commit",await refused(write("bulk_apply",{"preview_id":scope["preview_id"],"digest":scope["digest"]},reviewctx)))
            c.config=original_cfg;lib.ask_scoped=approve
            scope=await lib.bulk_preview(reviewctx,{"from":"2026-09-13","to":"2026-09-13"})
            lib.forget(reviewctx.session)
            check("disconnect forgets uncommitted review scopes",not lib.reviews and await refused(write("bulk_apply",{"preview_id":scope["preview_id"],"digest":scope["digest"]},reviewctx)))
            scope=await lib.bulk_preview(reviewctx,{"from":"2026-09-13","to":"2026-09-13"})
            await c.close();await c.start()
            check("restart retains saved versions but discards historical approval scopes",not lib.reviews and bool((await lib.catalog(context(cfg),{"query":"Sunday batch"}))["entries"]))
            ordinary_config=c.config;store_config=c.store.config
            c.config=replace(c.config,nutrition=replace(c.config.nutrition,bulk_max_meals=1))
            check("an oversized historical scope refuses instead of truncating its meals",await refused(lib.bulk_preview(context(cfg),{"from":"2026-09-13","to":"2026-09-13"})))
            c.config=replace(ordinary_config,nutrition=replace(ordinary_config.nutrition,bulk_max_previews=1))
            await lib.bulk_preview(context(cfg),{"from":"2026-09-13","to":"2026-09-13"})
            check("pending historical reviews have an explicit capacity",await refused(lib.bulk_preview(context(cfg),{"from":"2026-09-13","to":"2026-09-13"})))
            lib.reviews.clear();c.config=ordinary_config
            c.store.config=replace(store_config,nutrition=replace(store_config.nutrition,max_library_records=1))
            check("a full library refuses a new entry without evicting saved versions",await refused(write("catalog_save",{**food,"name":"Over capacity"})) and bool((await lib.catalog(context(cfg),{"query":"Sunday batch"}))["entries"]))
            c.store.config=replace(store_config,nutrition=replace(store_config.nutrition,max_plans_per_day=1))
            check("a full day of plans refuses another planned meal",await refused(write("plan_save",{**source,"source_revision":2,"day":"2026-09-13","revision":0})))
            c.store.config=store_config
            bad=context(cfg);bad.binding._lease.revoke()
            check("revoked owner authority cannot read the library or write a plan",await refused(lib.read(bad,"catalog",{})) and await refused(write("plan_save",{**food,"day":"2026-09-14"},bad)))
        finally:
            await c.close()
    print(f"\nall {len(CHECKS)} checks passed")


if __name__=="__main__":asyncio.run(main())
