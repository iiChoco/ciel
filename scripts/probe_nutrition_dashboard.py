"""Probe diary trends whose gaps are still gaps.

Temporary diaries pin completed-day eligibility, explicit zero intake, current
unfinished and future days, historical targets versus expenditure, retained
allowances/dates, macro coverage and averages, cumulative gaps, bounded weekly
evidence, and plans outside intake. Manual weights retain units, use observed
calendar-window means, and share review, revisions, receipts, Undo, authority,
and reopen behavior. No private state, model, mic, or external network is used.
"""
from __future__ import annotations

import asyncio
import math
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from ciel.nutrition import NutritionController, occurrence, readiness
from probe_nutrition import context, meal, refused
from probe_nutrition_library import fixture

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}",flush=True)
    if not ok:sys.exit(1)


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ciel-dashboard-probe-") as tmp,patch.object(Path,"home",return_value=Path(tmp)):
        root=Path(tmp);cfg=fixture(root);questions=[];receipts=[]
        async def ask(question: str) -> bool:
            questions.append(question);return True
        async def emit(ctx: Any,receipt: dict[str,Any]) -> None:
            receipts.append(receipt)
        c=NutritionController(cfg,host="fixture-host",ask=ask,emit=emit);await c.start()
        async def apply(op: str,args: dict[str,Any],**kw: Any) -> dict[str,Any]:
            return await c.apply(context(cfg,**kw),op,args)
        async def food(day: str,name: str="Usual oats",*,estimate: bool=False,unknown: bool=False) -> dict[str,Any]:
            args=meal(name);args["occurred_at"]=day+"T12:00:00-07:00"
            args["items"][0].update(source="estimate" if estimate else "label",uncertainty=["estimate"] if estimate else [],carbs_g=20,protein_g=None if unknown else 10)
            return await apply("save",args)
        async def complete(day: str,zero: bool=False) -> None:
            await apply("complete",{"day":day,"revision":0,"complete":True,"zero_intake":zero})
        async def settings(day: str,burn: Any,target: Any=1800) -> None:
            await apply("settings",{"effective_from":day,"revision":0,"expenditure":burn,"calorie_target":target,"protein_target_g":100})
        async def read(first: str="2026-09-01",last: str="2026-09-07") -> dict[str,Any]:
            return await c.dashboard.read(context(cfg),{"from":first,"to":last})
        try:
            empty=await read()
            check("missing diary days are gaps rather than zero calories",all(r["totals"]["calories"] is None and r["status"]=="missing" for r in empty["days"]))
            check("an empty range has no invented deficit or averages",empty["summary"]["cumulative_deficit"] is None and empty["averages"]["calories"]["value"] is None)
            await settings("2026-08-31",None)
            await food("2026-09-01");await complete("2026-09-01")
            target=await read()
            check("an intake target never supplies expenditure",target["days"][0]["settings"]["calorie_target"]==1800 and target["days"][0]["deficit_exclusion"]=="expenditure_unset")
            await settings("2026-09-02",2000)
            second=await food("2026-09-02",estimate=True);await complete("2026-09-02")
            await food("2026-09-03")
            await complete("2026-09-04",True)
            await settings("2026-09-05",1800,1600)
            await food("2026-09-05",unknown=True);await complete("2026-09-05")
            sixth=await food("2026-09-06");await complete("2026-09-06")
            await apply("delete",{"id":sixth["record_id"],"revision":sixth["revision"]})
            source=(await c.library.apply(context(cfg),"catalog_save",{"kind":"food","name":"Planned food","revision":0,"items":meal()["items"]}))
            await c.library.apply(context(cfg),"plan_save",{"source_kind":"food","source_id":source["record_id"],"source_revision":1,"day":"2026-09-07","revision":0})
            result=await read();rows={r["day"]:r for r in result["days"]}
            check("expenditure and target changes apply from their recorded date",rows["2026-09-02"]["settings"]["expenditure"]==2000 and rows["2026-09-05"]["settings"]["expenditure"]==1800 and rows["2026-09-01"]["settings"]["expenditure"] is None)
            check("visible calorie allowances reduce the estimated deficit once",rows["2026-09-02"]["allowance"]==20 and rows["2026-09-02"]["deficit"]==1780)
            check("unfinished intake remains visible but contributes no deficit",rows["2026-09-03"]["totals"]["calories"]==200 and rows["2026-09-03"]["deficit"] is None)
            check("an explicit complete zero-intake day is a usable reading",rows["2026-09-04"]["totals"]["calories"]==0 and rows["2026-09-04"]["deficit"]==2000)
            check("deleting the last meal retains completion without inventing zero intake",rows["2026-09-06"]["complete"] and rows["2026-09-06"]["status"]=="empty_without_zero" and rows["2026-09-06"]["deficit"] is None)
            check("a planned meal never fills an intake gap",rows["2026-09-07"]["meal_count"]==0 and rows["2026-09-07"]["totals"]["calories"] is None)
            check("unknown protein is not zero on the graph",rows["2026-09-05"]["totals"]["protein_g"] is None and rows["2026-09-05"]["coverage"]["protein_g"]=={"known_items":0,"total_items":1})
            check("cumulative estimates stop at gaps and disclose their included days",rows["2026-09-03"]["cumulative_deficit"] is None and rows["2026-09-04"]["cumulative_deficit"]==3780 and result["summary"]=={"days":7,"complete_days":4,"deficit_days":3,"cumulative_deficit":5380,"gap_days":4})
            check("calorie and protein averages each name their usable completed days",result["averages"]["calories"]=={"value":155.0,"days":4} and result["averages"]["protein_g"]=={"value":3.33,"days":3})
            check("the weekly review uses seven days ending on the selected range",result["weekly_review"]["from"]=="2026-09-01" and result["weekly_review"]["complete_days"]==4)
            review=result["weekly_review"]
            check("recurring meals and allowance reviews carry exact source evidence",review["recurring"]["entries"][0]["count"]==4 and review["allowances"]["entries"][0]["id"]==second["record_id"] and review["allowances"]["entries"][0]["revision"]==1)
            check("the weekly review names missing nutrients without filling them",review["unresolved"]["entries"][0]["missing_nutrients"]==["protein_g"])
            history=len(await c.recent(context(cfg),50));await read();await read()
            check("dashboard and weekly reads never add journal history",len(await c.recent(context(cfg),50))==history)
            await settings("2026-09-08",100,1000);await food("2026-09-08");await complete("2026-09-08")
            check("a surplus has a negative sign even with a larger intake target",(await read("2026-09-08","2026-09-08"))["days"][0]["deficit"]==-100)
            await settings("2026-09-09",None,None);await food("2026-09-09");await complete("2026-09-09")
            check("clearing expenditure preserves historical estimates and creates later gaps",(await read("2026-09-08","2026-09-09"))["summary"]["deficit_days"]==1)
            current=occurrence({},cfg)["diary_date"];await food(current)
            check("the current unfinished day is not treated as finished",(await read(current,current))["days"][0]["deficit"] is None)
            await settings("2099-01-01",2000);await food("2099-01-01");await complete("2099-01-01")
            check("future completed entries cannot contribute historical averages",(await read("2099-01-01","2099-01-01"))["averages"]["calories"]["days"]==0)
            before=await read();c.config=replace(cfg,timezone="Asia/Tokyo",nutrition=replace(cfg.nutrition,diary_day_start_hour=9,estimate_allowance_pct=80))
            after=await read()
            check("travel cutoff and allowance changes do not move or reprice history",before["days"]==after["days"])
            c.config=cfg
            c.config=replace(cfg,nutrition=replace(cfg.nutrition,dashboard_review_limit=1))
            check("bounded review lists disclose omitted example counts",(await read())["weekly_review"]["recurring"]["entries"][0]["count"]==4 and len((await read())["weekly_review"]["recurring"]["entries"][0]["examples"])==1)
            c.config=replace(cfg,nutrition=replace(cfg.nutrition,dashboard_max_meals=1))
            check("an oversized meal range refuses rather than returning partial totals",await refused(read()))
            c.config=cfg
            check("custom date ranges reject reversal and excess length",await refused(read("2026-09-08","2026-09-01")) and await refused(read("2025-01-01","2026-09-01")))
            check("leap dates use inclusive calendar days",len((await read("2024-02-28","2024-03-01"))["days"])==3)
            check("invalid calendar dates are refused",await refused(read("2026-02-30","2026-03-01")))
            initial_questions=len(questions)
            args={"day":"2026-09-01","revision":0,"value":70,"unit":"kg"}
            saved=await apply("weight_save",args,identity="weight-once")
            retry=await apply("weight_save",args,identity="weight-once")
            check("page weigh-in Save authorizes its revision and has a durable retry receipt",saved==retry and len(questions)==initial_questions and receipts[-1]["text"].startswith("Saved the weigh-in"))
            check("a page weigh-in cannot reuse its request ID for different values",await refused(apply("weight_save",{**args,"value":71},identity="weight-once")))
            await apply("weight_save",{"day":"2026-09-03","revision":0,"value":154,"unit":"lb"},page=False)
            weights=await read();w=weights["days"][2]
            check("voice measurements use the broker and preserve entered pounds",len(questions)==initial_questions+1 and w["weight"]["value"]["unit"]=="lb" and math.isclose(w["weight"]["value"]["kg"],69.85322498))
            check("the weight trend averages observed readings without filling gaps",w["weight_trend"]["kg"]==69.9266 and weights["days"][1]["weight_trend"]["kg"] is None)
            check("a single weigh-in has no invented smoothed trend",weights["days"][0]["weight_trend"]["kg"] is None)
            narrow=await read("2026-09-03","2026-09-03")
            check("weight smoothing includes observations before the visible range",narrow["days"][0]["weight_trend"]==w["weight_trend"])
            await apply("weight_save",{"day":"2026-09-10","revision":0,"value":69,"unit":"kg"})
            check("old weigh-ins outside the calendar window cannot affect the mean",(await read("2026-09-10","2026-09-10"))["days"][0]["weight_trend"]["kg"] is None)
            changed=await apply("weight_save",{**args,"revision":1,"value":71})
            check("correcting a weigh-in replaces that day's observation",(await read())["days"][0]["weight"]["revision"]==2)
            check("a stale weigh-in edit refuses before overwriting",await refused(apply("weight_save",{**args,"revision":1,"value":72})))
            await apply("undo",{"operation_id":changed["operation_id"]})
            check("weigh-in Undo restores the prior observed value",(await read())["days"][0]["weight"]["value"]["value"]==70)
            revision=(await read())["days"][0]["weight"]["revision"]
            removed=await apply("weight_delete",{"day":"2026-09-01","revision":revision})
            check("removing a weigh-in leaves no observed point",(await read())["days"][0]["weight"]["value"] is None)
            await apply("undo",{"operation_id":removed["operation_id"]})
            check("removal Undo restores the observation and weight mean",(await read())["days"][2]["weight_trend"]["kg"]==69.9266)
            check("weigh-ins never change intake or deficit",(await read())["summary"]==result["summary"])
            check("page weigh-in changes require an explicit revision",await refused(apply("weight_save",{"day":"2026-09-11","value":70,"unit":"kg"})))
            for amount in (0,-1,True,float('nan'),1001):
                check("a nonpositive nonfinite or invalid weight is refused",await refused(apply("weight_save",{"day":"2026-09-11","revision":0,"value":amount,"unit":"kg"})))
            check("unsupported weight units are refused",await refused(apply("weight_save",{"day":"2026-09-11","revision":0,"value":70,"unit":"stone"})))
            revoked=context(cfg);revoked.binding._lease.revoke()
            check("revoked owner authority cannot read dashboard or save weight",await refused(c.dashboard.read(revoked,{})) and await refused(c.apply(revoked,"weight_save",args)))
            c.ask=None
            check("unavailable voice confirmation cannot save a weigh-in",await refused(apply("weight_save",{"day":"2026-09-11","revision":0,"value":70,"unit":"kg"},page=False)))
            await c.close();c=NutritionController(cfg,host="fixture-host");await c.start()
            check("weigh-ins and computed trends survive reopening the diary",(await read())["days"][2]["weight_trend"]["kg"]==69.9266)
            check("dashboard reads keep the current nutrition schema",await c.store.run(lambda:c.store._connection().execute('PRAGMA user_version').fetchone()[0])==3)
            await food("2026-08-10",unknown=True);await food("2026-08-10");await complete("2026-08-10")
            partial=await read("2026-08-10","2026-08-10")
            check("a partially known macro remains a disclosed subtotal outside full-day averages",partial["days"][0]["totals"]["protein_g"]==5 and partial["days"][0]["coverage"]["protein_g"]=={"known_items":1,"total_items":2} and partial["averages"]["protein_g"]["days"]==0)
            check("calendar boundaries refuse a range whose lookback would underflow",await refused(read("0001-01-01","0001-01-07")))
            c.config=replace(cfg,nutrition=replace(cfg.nutrition,dashboard_max_days=3))
            check("the default dashboard range respects a smaller configured bound",len((await c.dashboard.read(context(cfg),{}))["days"])==3 and (await c.view(context(cfg)))["dashboard_max_days"]==3)
            c.config=cfg
            bad=replace(cfg,nutrition=replace(cfg.nutrition,dashboard_max_days=367))
            check("readiness refuses dashboard limits beyond the hard bound",not readiness(bad,host="fixture-host")[0])
        finally:await c.close()
    print(f"\nall {len(CHECKS)} checks passed")


if __name__=="__main__":asyncio.run(main())
