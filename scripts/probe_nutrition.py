"""Probe the private nutrition diary without a model, network, or home state.

Pins atomic data/history/receipts, retry identities, exact snapshot repeats,
conditional confirmation, session-scoped Undo, revision conflicts, completion,
diary cutoffs and DST, units and missing macros, source snapshots, owner-only
storage, writer ownership, merged history, history capacity with Undo room,
receipt-delivery failure, bounded USDA parsing, distinguishable missing/empty/
unreadable credentials with no network request, secret-free provider failures,
and saved source/date survival after cache and settings changes. Every file belongs to this probe.
"""
from __future__ import annotations

import asyncio
import json
import io
import math
import os
import stat
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from ciel.config import Config
from ciel.nutrition import Change, FoodLookup, NutritionController, NutritionStore, OwnerContext, meal_values, occurrence, readiness
from ciel.task_context import TaskBinding
from ciel.turn import owner_origin

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


async def refused(call: Any) -> bool:
    try:
        await call
    except (ValueError, RuntimeError):
        return True
    return False


def context(cfg: Config, session: str = "session-a", *, page: bool = True, identity: str | None = None) -> OwnerContext:
    return OwnerContext(TaskBinding(owner_origin(cfg.tasks.owner, "web" if page else "voice", identity), 0, 0), session, page)


def meal(name: str = "Breakfast") -> dict[str, Any]:
    return {"name": name, "revision": 0, "occurred_at": "2026-09-13T09:00:00-07:00", "timezone": "America/Los_Angeles",
            "items": [{"name": "Oats", "quantity": 50, "unit": "g", "basis_quantity": 100, "basis_unit": "g",
                       "calories": 400, "protein_g": 10, "carbs_g": None, "fat_g": 8, "source": "label", "uncertainty": []}]}


class Lookup:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, query: str) -> list[dict[str, Any]]:
        self.calls += 1
        return [{**meal()["items"][0], "name": "USDA oats", "source": "usda", "source_id": "123", "quantity": 100}]


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ciel-nutrition-probe-") as tmp, patch.object(Path, "home", return_value=Path(tmp)):
        root = Path(tmp)
        cfg = Config()
        cfg = replace(cfg, state_dir=root, timezone="America/Los_Angeles",
                      hub=replace(cfg.hub, require_token=True, token="fixture-token", token_file=root / "missing-token"),
                      nutrition=replace(cfg.nutrition, enabled=True, owner_host="fixture-host", state_dir=root / "nutrition-state",
                                        fdc_api_key_file=root / "missing-key"))
        questions, receipts = [], []
        async def ask(question: str) -> bool:
            questions.append(question)
            return True
        async def emit(ctx: OwnerContext, receipt: dict[str, Any]) -> None:
            receipts.append((ctx, receipt))
        lookup = Lookup()
        c = NutritionController(cfg, host="fixture-host", ask=ask, emit=emit, lookup=lookup)
        await c.start()
        check("the designated host opens one private diary", c.store is not None and bool(c.store.diary_id))
        try:
            for name in ("nutrition.sqlite3", "owner.lock"):
                check(f"{name} is owner-only", stat.S_IMODE((c.store.directory / name).stat().st_mode) == 0o600)
            check("the whole diary directory is owner-only", stat.S_IMODE(c.store.directory.stat().st_mode) == 0o700)
            other = NutritionController(cfg, host="fixture-host")
            await other.start()
            check("a second writer cannot take the same diary", other.store is None)
            await other.close()
            check("the doctor refuses nutrition without required-token policy", not readiness(replace(cfg, hub=replace(cfg.hub, require_token=False)), host="fixture-host")[0])
            check("loopback does not waive a missing token", not readiness(replace(cfg, hub=replace(cfg.hub, token="")), host="fixture-host")[0])
            check("another host or runtime role cannot initialize a diary", not readiness(cfg, host="other")[0] and not readiness(cfg, host="fixture-host", role="local")[0])
            first_ctx = context(cfg, identity="first-save")
            first = await c.apply(first_ctx, "save", meal())
            state = await c.view(first_ctx, "2026-09-13")
            original = state["meals"][0]
            check("one reviewed page Save records data and history without a broker question", len(state["meals"]) == 1 and len(state["history"]) == 1 and not questions)
            check("source basis is multiplied by the measured portion", state["totals"]["calories"] == 200 and state["totals"]["protein_g"] == 5)
            check("unknown carbohydrate remains unknown with incomplete coverage", state["totals"]["carbs_g"] is None and state["coverage"]["carbs_g"]["known_items"] == 0)
            check("known label portions receive no extra allowance", original["allowance"] == 0)
            check("the receipt names the committed meal and calorie total", first["record_id"] == original["id"] and "200 calories" in first["text"] and receipts[-1][1] == first)
            retry = await c.apply(first_ctx, "save", meal())
            check("the same request returns the same durable receipt without another meal", retry == first and len((await c.view(first_ctx, "2026-09-13"))["meals"]) == 1)
            check("a reused request ID cannot change its reviewed values", await refused(c.apply(first_ctx, "save", meal("Different"))))
            check("page Save requires its reviewed revision", await refused(c.apply(context(cfg), "save", {k:v for k,v in meal().items() if k != "revision"})))
            repeat_ctx = context(cfg, page=False)
            repeated = await c.apply(repeat_ctx, "repeat", {"source_id": original["id"], "occurred_at": "2026-09-13T12:00:00-07:00"})
            rows = (await c.view(repeat_ctx, "2026-09-13"))["meals"]
            copy = next(m for m in rows if m["id"] == repeated["record_id"])
            check("an explicit repeat creates a distinct meal with no broker question", len(rows) == 2 and copy["id"] != original["id"] and not questions)
            check("repeat preserves the complete food and allowance snapshots", all(copy[k] == original[k] for k in ("items","totals","allowance","policy")))
            check("a repeat emits its committed receipt without model acknowledgement", receipts[-1][1] == repeated and receipts[-1][0].binding.origin.lane == "voice")
            undo_ctx = context(cfg, page=False)
            undone = await c.apply(undo_ctx, "undo", {})
            check("immediate same-session Undo needs no extra yes", not questions and len((await c.view(undo_ctx,"2026-09-13"))["meals"]) == 1)
            check("Undo emits its own receipt and keeps the original in history", undone["undo_of"] == repeated["operation_id"] and receipts[-1][1] == undone and len(await c.recent(undo_ctx)) == 3)
            check("an Undo retry cannot reverse twice", await c.apply(undo_ctx,"undo",{}) == undone)
            check("Undo-of-Undo never toggles a saved operation back", await refused(c.apply(context(cfg,page=False), "undo", {})))
            await c.apply(context(cfg,page=False), "repeat", {"source_id": original["id"], "occurred_at": "2026-09-13T13:00:00-07:00"})
            await c.apply(context(cfg,"other-session",page=False), "undo", {})
            check("another session must answer a broker question before Undo", len(questions) == 1)
            before_questions = len(questions)
            voice = await c.apply(context(cfg,page=False), "save", meal("Lunch"))
            check("voice-proposed values pass the controller's broker exactly once", len(questions) == before_questions + 1)
            await c.apply(context(cfg), "save", {**meal("Corrected lunch"), "id": voice["record_id"], "revision": 1})
            check("a stale meal revision cannot overwrite a correction", await refused(c.apply(context(cfg), "save", {**meal(), "id": voice["record_id"], "revision": 1})))
            check("an older Undo cannot overwrite a newer revision", await refused(c.apply(context(cfg), "undo", {"operation_id": voice["operation_id"]})))
            await c.apply(context(cfg), "complete", {"day":"2026-09-13","complete":True,"revision":0})
            await c.apply(context(cfg), "save", meal("Forgotten snack"))
            check("reviewed additions preserve the owner's completion assertion", (await c.view(context(cfg),"2026-09-13"))["completion"]["value"]["complete"])
            check("an empty day cannot silently count as zero intake", await refused(c.apply(context(cfg),"complete",{"day":"2026-09-12","complete":True,"revision":0})))
            await c.apply(context(cfg),"complete",{"day":"2026-09-12","complete":True,"zero_intake":True,"revision":0})
            check("explicit zero intake can complete an empty day", (await c.view(context(cfg),"2026-09-12"))["completion"]["value"]["zero_intake"])
            noisy = meal()
            noisy["items"][0].update(source="estimate",uncertainty=["portion","portion","estimate"])
            values = meal_values(noisy,cfg)
            check("distinct uncertainty reasons form one deterministic allowance", values["allowance"] == 50 and values["totals"]["calories"] == 250)
            check("reanalysis replaces rather than compounds the allowance", meal_values(noisy,cfg) == values)
            bad = meal()
            bad["items"][0]["calories"] = math.nan
            check("nonfinite nutrients never enter arithmetic or storage", await refused(c.apply(context(cfg),"save",bad)))
            bad = meal()
            bad["items"][0]["unit"] = "ml"
            check("a volume does not become a mass through a guessed density", await refused(c.apply(context(cfg),"save",bad)))
            early = occurrence({"occurred_at":"2026-09-13T01:00:00-07:00"},cfg)
            check("one AM belongs to the previous diary day under the local cutoff", early["diary_date"] == "2026-09-12" and early["cutoff"] == 4)
            for instant in ("2026-03-08T02:30:00", "2026-11-01T01:30:00"):
                try: occurrence({"occurred_at":instant},cfg)
                except ValueError: denied=True
                else: denied=False
                check(f"{instant} needs a real unambiguous local instant",denied)
            await c.search(context(cfg), "oats")
            cached = await c.search(context(cfg), "oats")
            check("cached lookup records avoid another provider request", lookup.calls == 1 and cached["cached"])
            forged = meal()
            forged["items"][0].update(source="usda",source_id="123",calories=1)
            saved = await c.apply(context(cfg),"save",forged)
            source = await c.store.run(c.store._record,"meal",saved["record_id"])
            check("USDA saves use the retained source values, not model replacement numbers",source["value"]["totals"]["calories"] == 200)
            await c.store.run(lambda:c.store._connection().execute("DELETE FROM cache"))
            edited = await c.apply(context(cfg),"save",{**forged,"id":saved["record_id"],"revision":1})
            retained = await c.store.run(c.store._record,"meal",edited["record_id"])
            check("saved USDA source values survive cache eviction and a reviewed edit",retained["value"]["totals"]["calories"]==200)
            c.config=replace(cfg,nutrition=replace(cfg.nutrition,diary_day_start_hour=12))
            await c.apply(context(cfg),"save",{**meal("Revised breakfast"),"id":original["id"],"revision":1})
            kept = await c.store.run(c.store._record,"meal",original["id"])
            check("an unchanged eating time retains its saved cutoff when settings change",kept["value"]["diary_date"]==original["diary_date"] and kept["value"]["cutoff"]==4)
            c.config=cfg
            empty_change=Change("day","2026-09-11",0,{"complete":True,"zero_intake":False},"complete")
            check("commit rechecks that an unasserted empty day cannot become complete",await refused(c.store.run(c.store._commit,context(cfg),"empty-race","fixture-digest",empty_change)))
            expired = context(cfg)
            expired.binding._lease.revoke()
            check("revoked turn authority cannot read or write the diary", await refused(c.view(expired)) and await refused(c.apply(expired,"save",meal())))
            outsider = replace(context(cfg),binding=TaskBinding(owner_origin("somebody-else","voice"),0,0))
            check("another principal has no nutrition authority",await refused(c.view(outsider)))
            history_before = await c.recent(context(cfg),50)
            def break_commit() -> None:
                c.store._connection().execute("CREATE TRIGGER fail_history BEFORE INSERT ON history BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
            await c.store.run(break_commit)
            try:
                await c.apply(context(cfg),"save",meal("Should roll back"))
            except Exception:
                pass
            else:
                check("a history failure aborts Save",False)
            check("a failed history insert rolls back the meal and receipt too",await c.recent(context(cfg),50) == history_before and not any(m["name"] == "Should roll back" for m in (await c.view(context(cfg),"2026-09-13"))["meals"]))
            await c.store.run(lambda:c.store._connection().execute("DROP TRIGGER fail_history"))
            history=await c.inspect_history(context(cfg),{"record_id":original["id"]})
            check("one meal's older history remains inspectable independently of the recent list",len(history["history"])==2 and any(e["args"]["operation_id"]==first["operation_id"] for e in history["history"]))
            older=await c.inspect_history(context(cfg),{"before_seq":history_before[0]["seq"]})
            check("history pagination advances strictly to older operations",all(e["seq"]<history_before[0]["seq"] for e in older["history"]))
            parallel=context(cfg)
            both=await asyncio.gather(c.apply(parallel,"save",meal("Parallel retry")),c.apply(parallel,"save",meal("Parallel retry")))
            check("concurrent identical requests share one transaction receipt",both[0]==both[1] and sum(m["name"]=="Parallel retry" for m in (await c.view(context(cfg),"2026-09-13"))["meals"])==1)
            async def deny(question: str) -> bool:
                return False
            c.ask=deny
            check("a refused broker question commits no proposed meal",await refused(c.apply(context(cfg,page=False),"save",meal("Denied"))))
            check("a model's approval flag cannot supply owner authority",await refused(c.apply(context(cfg,page=False),"save",{**meal("Claimed"),"confirmed":True,"page":True})))
            async def broken_delivery(ctx: OwnerContext,receipt: dict[str,Any]) -> None:
                raise OSError("fixture delivery failure")
            c.emit=broken_delivery
            result=await c.apply(context(cfg),"save",meal("Delivery failure"))
            check("receipt-delivery failure still reports the committed operation",bool(result.get("delivery_error")) and any(m["id"]==result["record_id"] for m in (await c.view(context(cfg),"2026-09-13"))["meals"]))
            c.ask,c.emit=ask,emit
            batch=context(cfg,page=False)
            one=await c.apply(batch,"save",meal("First meal in one request"))
            two=await c.apply(batch,"save",meal("Second meal in one request"))
            check("one owner turn can review two distinct meals while retries remain stable",one["operation_id"]!=two["operation_id"] and await c.apply(batch,"save",meal("Second meal in one request"))==two)
            changed=replace(cfg,nutrition=replace(cfg.nutrition,portion_allowance_pct=20))
            check("changing the allowance mapping changes its policy identity",meal_values(noisy,changed)["policy"]["version"]!=values["policy"]["version"])
            history_before=await c.recent(context(cfg),50)
            diary_id=c.store.diary_id
        finally:
            await c.close()
        c=NutritionController(cfg,host="fixture-host",ask=ask,emit=emit)
        await c.start()
        try:
            check("restart preserves the diary identity and undo history",c.store.diary_id == diary_id and len(await c.recent(context(cfg),50)) == len(history_before))
        finally:
            await c.close()
        limited=replace(cfg,nutrition=replace(cfg.nutrition,state_dir=root/"limited-foods",max_records=1))
        c=NutritionController(limited,host="fixture-host")
        await c.start()
        try:
            saved=await c.apply(context(limited),"save",meal())
            check("history capacity refuses another original operation",await refused(c.apply(context(limited),"save",meal("Over capacity"))))
            undone=await c.apply(context(limited),"undo",{"operation_id":saved["operation_id"]})
            check("the last admitted change keeps room for Undo at capacity",undone["undo_of"]==saved["operation_id"])
        finally:
            await c.close()
        key=root/"fixture-key"
        for state in ("missing", "empty", "unreadable"):
            if state == "empty":
                key.write_text("   ")
            elif state == "unreadable":
                key.unlink()
                key.mkdir()
            unconfigured=FoodLookup(replace(cfg,nutrition=replace(cfg.nutrition,fdc_api_key_file=key)))
            with patch("urllib.request.urlopen") as fetch:
                try:
                    unconfigured.search("oats")
                except ValueError as exc:
                    message=str(exc)
                else:
                    message=""
                expected="cannot be read" if state == "unreadable" else state
                check(f"the {state} USDA key names the setup problem without a network request",
                      "not configured" in message and expected in message and not fetch.called and str(key) not in message)
                check(f"the {state} USDA key leaves a reviewed estimate available",
                      "clearly labeled estimate for review" in message and "unknown macros stay unknown" in message)
        key.rmdir()
        key.write_text("synthetic-usda-key")
        provider=FoodLookup(replace(cfg,nutrition=replace(cfg.nutrition,fdc_api_key_file=key)))
        payload={"foods":[{"fdcId":123,"description":"Fixture oats","dataType":"Foundation","foodNutrients":[
            {"nutrientId":1008,"unitName":"KCAL","value":400},
            {"nutrientId":1003,"unitName":"G","value":10},
            {"nutrientId":1005,"unitName":"MG","value":25}]}]}
        with patch("urllib.request.urlopen",return_value=io.BytesIO(json.dumps(payload).encode())) as fetch:
            foods=provider.search("oats")
            req=fetch.call_args.args[0]
            body=json.loads(req.data)
            check("USDA requests use a bounded search and a finite timeout",body["pageSize"]==8 and fetch.call_args.kwargs["timeout"]==8)
            check("USDA values keep their per-100-gram basis and reject wrong nutrient units",foods[0]["basis_quantity"]==100 and foods[0]["calories"]==400 and foods[0]["carbs_g"] is None)
        bounded=FoodLookup(replace(cfg,nutrition=replace(cfg.nutrition,fdc_api_key_file=key,lookup_max_bytes=10)))
        with patch("urllib.request.urlopen",return_value=io.BytesIO(b"x"*11)):
            check("an oversized USDA response is refused before parsing",await refused(asyncio.to_thread(bounded.search,"oats")))
        with patch("urllib.request.urlopen",side_effect=OSError("synthetic-usda-key in a failed URL")):
            try:
                provider.search("oats")
            except ValueError as exc:
                check("provider failures do not expose the API key", "synthetic-usda-key" not in str(exc) and "explicit nutrition values" in str(exc) and "lookup failed" in str(exc) and "not configured" not in str(exc))
            else:
                check("provider failures do not expose the API key",False)
    print(f"\nall {len(CHECKS)} checks passed")


if __name__ == "__main__":
    asyncio.run(main())
