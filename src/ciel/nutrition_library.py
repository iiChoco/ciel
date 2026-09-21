"""Familiar food keeps the version the owner ate.

**A name resolves to a snapshot.** Saved foods, recipes, and prepared batches
carry source values, preparation, yield, and the calorie policy. Editing a
recipe changes future use; a batch and a consumed meal retain their copies.
A changed portion is calculated from source bases under that saved policy.

**Planning is separate from eating.** Previews write nothing. Saved plans
live outside intake, and logging one atomically consumes the plan and writes
the meal, with one history entry and an inverse for both records.

**History changes only under a reviewed scope.** Bulk recalculation changes
allowances on named meal revisions, never source values or diary dates. Its
preview and approval live only in memory, belong to one private page session,
and expire. Both recalculation and its Undo require the scoped broker; a
normal yes or a model claim cannot authorize either operation.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from ciel.nutrition import Change, NUTRIENTS, UNITS, OwnerContext, diary_day, label, meal_values, number, occurrence, packed, policy

if TYPE_CHECKING:
    from ciel.nutrition import NutritionController

KINDS = ("food", "recipe", "batch")
READS = ("catalog", "portion", "preview", "plans", "bulk_preview")
WRITES = ("catalog_save", "catalog_delete", "batch_create", "catalog_log", "plan_save", "plan_delete", "plan_log", "bulk_apply")


def clone(value: Any) -> Any:
    return json.loads(packed(value))


def snapshot(value: dict[str, Any]) -> dict[str, Any]:
    return clone({k:value[k] for k in ("name","items","allowance","policy","totals","coverage")})


def scale(value: dict[str, Any], factor: float, controller: NutritionController) -> dict[str, Any]:
    number(factor,"portion factor",maximum=1000)
    if factor <= 0:
        raise ValueError("A portion must be positive.")
    if factor == 1:
        return snapshot(value)
    margins = value["policy"]["percentages"]
    config = replace(controller.config,nutrition=replace(controller.config.nutrition,
        portion_allowance_pct=margins["portion"],oil_allowance_pct=margins["oil"],estimate_allowance_pct=margins["estimate"]))
    items = [{**i,"quantity":i["quantity"]*factor} for i in value["items"]]
    return meal_values({"name":value["name"],"items":items},config)


@dataclass(frozen=True)
class Review:
    id: str
    owner: str
    session: str
    expires: float
    digest: str
    policy: dict[str, float]
    changes: tuple[Change, ...]
    report: dict[str, Any]


class NutritionLibrary:
    def __init__(self, controller: NutritionController) -> None:
        self.controller = controller
        self.reviews: dict[str, Review] = {}
        self.ask_scoped: Callable[[OwnerContext, str, str, dict[str, Any], float], Awaitable[bool]] | None = None

    async def _record(self, context: OwnerContext, kind: str, record_id: Any, revision: Any = None) -> dict[str, Any]:
        store = self.controller._admit(context)
        row = await self.controller._read(context,store._record,kind,label(record_id,"record ID",100))
        if row["value"] is None:
            raise ValueError("That saved record is absent; choose an existing entry.")
        if revision is not None and (type(revision) is not int or revision != row["revision"]):
            raise ValueError("The saved source changed; review its current version.")
        return row

    def _identity(self, context: OwnerContext, operation: str, args: dict[str, Any]) -> tuple[str, str]:
        if not isinstance(args,dict) or len(packed(args).encode()) > self.controller.config.nutrition.max_request_bytes:
            raise ValueError("Nutrition request is too large.")
        digest = hashlib.sha256(packed({"operation":operation,"args":args}).encode()).hexdigest()
        origin = context.binding.origin
        identity = f"{origin.owner}:{origin.request_id}:nutrition:{operation}"
        return hashlib.sha256((identity if context.page else identity+":"+digest).encode()).hexdigest(),digest

    async def values(self, context: OwnerContext, args: dict[str, Any], prior: dict[str, Any] | None = None) -> dict[str, Any]:
        items = args.get("items")
        if not isinstance(items,list) or not 1 <= len(items) <= self.controller.config.nutrition.max_items:
            raise ValueError("Choose a bounded list of foods with source values.")
        resolved = []
        for item in items:
            if not isinstance(item,dict):
                raise ValueError("Each food must be an object.")
            if item.get("source") == "usda":
                source_id = str(item.get("source_id",""))
                retained = next((i for i in (prior or {}).get("items",[]) if i.get("source")=="usda" and i.get("source_id")==source_id),None)
                if retained is None:
                    def cached() -> Any:
                        row = self.controller.store._connection().execute("SELECT body FROM cache WHERE id=?",(source_id,)).fetchone()
                        return json.loads(row[0]) if row else None
                    retained = await self.controller._read(context,cached)
                if retained is None:
                    raise ValueError("That USDA source is unavailable; search again or enter supplied label values.")
                item = {**retained,"quantity":item.get("quantity",100),"unit":item.get("unit","g"),"uncertainty":item.get("uncertainty",[])}
            resolved.append(item)
        fraction = number(args.get("consumed_fraction",1),"consumed fraction",maximum=1)
        if not fraction:raise ValueError("A proposed portion must be positive.")
        resolved = [{**i,"quantity":number(i.get("quantity",1),"quantity")*fraction} for i in resolved]
        return meal_values({"name":args.get("name","Meal"),"items":resolved},self.controller.config)

    async def catalog(self, context: OwnerContext, args: dict[str, Any]) -> dict[str, Any]:
        kind = args.get("kind")
        requested_id = args.get("id")
        if requested_id is not None:label(requested_id,"record ID",100)
        if kind is not None and kind not in KINDS:
            raise ValueError("Choose food, recipe, or batch.")
        query = str(args.get("query","")).casefold().strip()
        cursor = str(args.get("cursor",""))
        if len(cursor)>150:raise ValueError("Invalid library cursor.")
        if len(query)>160:
            raise ValueError("Food search is too long.")
        def read() -> list[dict[str, Any]]:
            db = self.controller.store._connection()
            rows = [self.controller.store._record(r[0],r[1]) for r in db.execute("SELECT kind,id FROM records WHERE kind IN ('food','recipe','batch') AND body IS NOT NULL ORDER BY kind,id")]
            return [r for r in rows if (requested_id is None or r["id"]==requested_id) and r["kind"]+":"+r["id"]>cursor and (kind is None or r["kind"]==kind) and (not query or any(query in n.casefold() for n in (r["value"]["name"],*r["value"].get("aliases",[]))))]
        rows = await self.controller._read(context,read)
        return {"entries":rows[:50],"next_cursor":rows[49]["kind"]+":"+rows[49]["id"] if len(rows)>50 else None}

    async def portion(self, context: OwnerContext, args: dict[str, Any]) -> dict[str, Any]:
        kind = args.get("source_kind")
        if kind not in KINDS:
            raise ValueError("Choose one saved food, recipe, or batch by ID.")
        if type(args.get("source_revision")) is not int:
            raise ValueError("A saved portion must name the source revision.")
        row = await self._record(context,kind,args.get("source_id"),args["source_revision"])
        value = row["value"]
        if "fraction" in args:
            if "quantity" in args or "unit" in args:
                raise ValueError("Choose either a fraction of the whole yield or an amount and unit.")
            factor = number(args["fraction"],"fraction of the whole",maximum=1)
        else:
            quantity = number(args.get("quantity",1),"portion quantity")
            unit = args.get("unit",value["yield"]["unit"])
            basis_unit = value["yield"]["unit"]
            if unit not in UNITS or UNITS[unit][0] != UNITS[basis_unit][0]:
                raise ValueError("Use the yield's serving, mass, or volume basis; density is not guessed.")
            factor = quantity*UNITS[unit][1]/(value["yield"]["quantity"]*UNITS[basis_unit][1])
        result = scale(value["snapshot"],factor,self.controller)
        return {"meal":result,"source":{"kind":kind,"id":row["id"],"revision":row["revision"]},"factor":factor,"yield":value["yield"]}

    async def plans(self, context: OwnerContext, args: dict[str, Any]) -> dict[str, Any]:
        day = diary_day(args.get("day") or occurrence({},self.controller.config)["diary_date"])
        def read() -> tuple[list[dict[str, Any]], dict[str, Any]]:
            rows = [self.controller.store._record("plan",r[0]) for r in self.controller.store._connection().execute("SELECT id FROM records WHERE kind='plan' AND body IS NOT NULL AND json_extract(body,'$.day')=? AND json_extract(body,'$.logged')=0 ORDER BY id",(day,))]
            return rows,self.controller.store._day(day)
        rows,actual = await self.controller._read(context,read)
        return {"day":day,"plans":rows,**self._projection(actual,[r["value"]["snapshot"] for r in rows])}

    @staticmethod
    def _projection(actual: dict[str, Any], meals: list[dict[str, Any]]) -> dict[str, Any]:
        totals,coverage = {},{}
        for key in NUTRIENTS:
            values = [actual["totals"][key] if actual["coverage"][key]["known_items"] else None,*[m["totals"][key] for m in meals]]
            known = [v for v in values if v is not None]
            coverage[key] = {"known_items":actual["coverage"][key]["known_items"]+sum(m["coverage"][key] for m in meals),
                            "total_items":actual["coverage"][key]["total_items"]+sum(len(m["items"]) for m in meals)}
            totals[key] = round(sum(known),2) if known else (None if coverage[key]["total_items"] else 0)
        return {"logged":actual["totals"],"projected":totals,"coverage":coverage,"planned_count":len(meals),"calorie_target":actual["settings"].get("calorie_target")}

    async def preview(self, context: OwnerContext, args: dict[str, Any]) -> dict[str, Any]:
        day = diary_day(args.get("day") or occurrence({},self.controller.config)["diary_date"])
        meal = (await self.portion(context,args))["meal"] if args.get("source_id") else await self.values(context,args)
        actual = await self.controller.view(context,day)
        return {"day":day,"meal":meal,"hypothetical":True,**self._projection(actual,[meal])}

    async def read(self, context: OwnerContext, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        self.controller._admit(context)
        self._identity(context,operation,args)
        return await getattr(self,operation)(context,args)

    async def apply(self, context: OwnerContext, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        c = self.controller;store = c._admit(context)
        identity,digest = self._identity(context,operation,args)
        old = await c._read(context,store._receipt,identity,digest)
        if old is not None:
            return await c._deliver(context,old)
        if operation == "bulk_apply":
            return await self.apply_bulk(context,identity,digest,args)
        record_id = label(args["id"],"record ID",100) if args.get("id") else str(uuid.uuid5(uuid.NAMESPACE_URL,identity))
        direct = False
        guards: tuple[tuple[str,str,int], ...] = ()
        if operation in ("catalog_save","catalog_delete","batch_create"):
            kind = "batch" if operation == "batch_create" else args.get("kind")
            if kind not in (KINDS if operation == "catalog_delete" else ("food","recipe","batch")) or kind=="batch" and operation=="catalog_save":
                raise ValueError("Save a food or recipe, or create a batch from a recipe.")
            current = await c._read(context,store._record,kind,record_id)
            revision = args.get("revision")
            if operation == "batch_create":revision = 0
            if operation == "catalog_delete":
                if current["value"] is None:raise ValueError("That saved entry is absent.")
                value = None
            else:
                if operation == "batch_create":
                    source = await self._record(context,"recipe",args.get("recipe_id"),args.get("recipe_revision"))
                    if type(args.get("recipe_revision")) is not int:raise ValueError("Choose the recipe revision.")
                    guards = (("recipe",source["id"],source["revision"]),)
                    value = clone(source["value"])
                    value.update(name=label(args.get("name"),"batch name"),aliases=[],recipe={"id":source["id"],"revision":source["revision"]},created_at=time.time())
                else:
                    if args.get("source_meal_id"):
                        source = await self._record(context,"meal",args["source_meal_id"],args.get("source_meal_revision"))
                        if type(args.get("source_meal_revision")) is not int:raise ValueError("Choose the source meal revision.")
                        if args.get("items"):raise ValueError("Copy the saved meal or supply edited foods, not both.")
                        values = snapshot(source["value"]);guards = (("meal",source["id"],source["revision"]),)
                    else:
                        values = await self.values(context,args,(current["value"] or {}).get("snapshot"))
                    name = label(args.get("name"),"saved name");values["name"] = name
                    aliases = args.get("aliases",[])
                    if not isinstance(aliases,list) or len(aliases)>8:raise ValueError("Use at most eight aliases.")
                    aliases = list(dict.fromkeys(label(a,"alias",80) for a in aliases))
                    quantity = 1 if kind=="food" else number(args.get("yield_quantity"),"batch yield")
                    unit = "serving" if kind=="food" else args.get("yield_unit")
                    if not quantity or unit not in UNITS:raise ValueError("A recipe needs a positive yield with a supported unit.")
                    value = {"name":name,"aliases":aliases,"snapshot":values,"yield":{"quantity":quantity,"unit":unit}}
            change = Change(kind,record_id,revision,value,operation,guards=guards)
        elif operation in ("catalog_log","plan_log"):
            if operation == "catalog_log":
                portion = await self.portion(context,args)
                value = portion["meal"];source = portion["source"]
                value["saved_from"] = source
                guards = ((source["kind"],source["id"],source["revision"]),)
                direct = source["kind"]=="food" and portion["factor"]==1
                related = ()
            else:
                row = await self._record(context,"plan",args.get("plan_id"),args.get("plan_revision"))
                if type(args.get("plan_revision")) is not int or row["value"]["logged"]:raise ValueError("Choose an unconsumed plan at its current revision.")
                value = clone(row["value"]["snapshot"])
                value["planned_from"] = {"id":row["id"],"revision":row["revision"]}
                related = (Change("plan",row["id"],row["revision"],{**row["value"],"logged":True,"meal_id":record_id},operation),)
                if not args.get("occurred_at"):raise ValueError("Choose the actual eating time before logging a plan.")
            value.update(occurrence(args,c.config))
            change = Change("meal",record_id,0,value,operation,related=related,guards=guards)
        elif operation in ("plan_save","plan_delete"):
            current = await c._read(context,store._record,"plan",record_id)
            if current["value"] and current["value"]["logged"]:raise ValueError("That plan was already logged; edit the consumed meal.")
            if operation == "plan_delete":
                if current["value"] is None:raise ValueError("That plan is absent.")
                value = None
            else:
                if args.get("source_id"):
                    portion = await self.portion(context,args);values = portion["meal"];source = portion["source"]
                    guards = ((source["kind"],source["id"],source["revision"]),)
                else:
                    values = await self.values(context,args,(current["value"] or {}).get("snapshot"))
                value = {"name":values["name"],"day":diary_day(args.get("day")),"snapshot":values,"logged":False}
            change = Change("plan",record_id,args.get("revision"),value,operation,guards=guards)
        else:
            raise ValueError("Unknown library change.")
        if type(change.revision) is not int or change.revision<0:
            raise ValueError("A reviewed change needs the current revision, or zero for a new entry.")
        if not context.page and not direct:
            if c.ask is None or not await c.ask("Apply this nutrition change? Names are quoted data. "+packed({"action":operation,"kind":change.kind,"proposal":change.value})):
                raise ValueError("Nutrition change was not approved.")
        c._admit(context)
        return await c._deliver(context,await store.run(store._commit,context,identity,digest,change))

    def _prune_reviews(self) -> None:
        self.reviews = {key:r for key,r in self.reviews.items() if r.expires>time.monotonic()}

    def forget(self, session: str) -> None:
        self.reviews = {key:r for key,r in self.reviews.items() if r.session!=session}

    async def bulk_preview(self, context: OwnerContext, args: dict[str, Any]) -> dict[str, Any]:
        if not self.controller.config.confirm.ask_first:
            raise ValueError("Historical recalculation requires confirmations to be on.")
        start,end = diary_day(args.get("from")),diary_day(args.get("to"))
        if start>end:raise ValueError("The first diary date must precede the last.")
        limit = self.controller.config.nutrition.bulk_max_meals
        def read() -> list[dict[str, Any]]:
            return [self.controller.store._record("meal",r[0]) for r in self.controller.store._connection().execute("SELECT id FROM records WHERE kind='meal' AND body IS NOT NULL AND json_extract(body,'$.diary_date') BETWEEN ? AND ? ORDER BY json_extract(body,'$.diary_date'),id LIMIT ?",(start,end,limit+1))]
        rows = await self.controller._read(context,read)
        if not rows:raise ValueError("No consumed meals in this date range.")
        if len(rows)>limit:raise ValueError(f"Review at most {limit} meals at once; choose a smaller date range.")
        changes,details = [],[]
        for row in rows:
            old = row["value"]
            new = {**old,**meal_values(old,self.controller.config)}
            changes.append(Change("meal",row["id"],row["revision"],new,"bulk_apply"))
            details.append({"id":row["id"],"revision":row["revision"],"name":old["name"],"day":old["diary_date"],
                            "before":old["totals"]["calories"],"after":new["totals"]["calories"],"before_allowance":old["allowance"],"after_allowance":new["allowance"]})
        report = {"from":start,"to":end,"meals":details,"count":len(rows),"policy":policy(self.controller.config),
                  "before":round(sum(d["before"] for d in details),2),"after":round(sum(d["after"] for d in details),2),
                  "explanation":"Only calorie allowances are recalculated. Food values, portions, dates, and completion stay as saved."}
        digest = hashlib.sha256(packed({"report":report,"changes":[c.value for c in changes]}).encode()).hexdigest()
        if not context.page:
            return {**report,"digest":digest,"page_required":True,"instruction":"Review and apply this date range on the private nutrition page."}
        self._prune_reviews()
        if len(self.reviews)>=self.controller.config.nutrition.bulk_max_previews:
            raise ValueError("The review slots are full; finish or wait for an existing review to expire.")
        identity = str(uuid.uuid4());expires = time.monotonic()+self.controller.config.nutrition.bulk_preview_lifetime_s
        self.reviews[identity] = Review(identity,context.binding.origin.owner,context.session,expires,digest,policy(self.controller.config),tuple(changes),clone(report))
        return {**report,"preview_id":identity,"digest":digest,"expires_in":self.controller.config.nutrition.bulk_preview_lifetime_s}

    async def _strict(self, context: OwnerContext, identity: str, digest: str, changes: tuple[Change,...], report: dict[str, Any],
                      review_digest: str, expires: float, expected_policy: dict[str,float], undo_of: str | None = None) -> dict[str, Any]:
        c = self.controller;store = c._admit(context)
        expires = min(expires,time.monotonic()+c.config.web.confirm_timeout_s)
        def guard() -> None:
            if not c.config.confirm.ask_first:raise ValueError("Historical recalculation requires confirmations to be on.")
            if time.monotonic()>=expires:raise ValueError("This historical review expired; review again.")
            if policy(c.config)!=expected_policy:raise ValueError("The calorie policy changed; review again.")
        guard()
        if not context.page or not context.session or self.ask_scoped is None:
            raise ValueError("Review and approve historical recalculation and its Undo on the private nutrition page.")
        if not await self.ask_scoped(context,"bulk_undo" if undo_of else "bulk_apply",review_digest,report,expires):
            raise ValueError("Historical change was not approved; nothing changed.")
        guard();c._admit(context)
        first = changes[0]
        change = replace(first,action="undo" if undo_of else "bulk_apply",undo_of=undo_of,related=changes[1:],strict=True)
        return await c._deliver(context,await store.run(store._commit,context,identity,digest,change,False,guard))

    async def apply_bulk(self, context: OwnerContext, identity: str, digest: str, args: dict[str, Any]) -> dict[str, Any]:
        self._prune_reviews()
        review = self.reviews.get(args.get("preview_id"))
        if review is None or review.owner!=context.binding.origin.owner or review.session!=context.session or args.get("digest")!=review.digest:
            raise ValueError("This review is absent, expired, or belongs to another page. Review the range again.")
        try:
            return await self._strict(context,identity,digest,review.changes,review.report,review.digest,review.expires,review.policy)
        finally:
            self.reviews.pop(review.id,None)

    async def undo_bulk(self, context: OwnerContext, identity: str, digest: str, record: dict[str, Any]) -> dict[str, Any]:
        before,after = json.loads(record["before_value"])["records"],json.loads(record["after_value"])["records"]
        changes = tuple(Change(b["kind"],b["id"],a["revision"],b["value"],"undo") for b,a in zip(before,after))
        report = {"undo_of":record["operation_id"],"count":len(changes),"meals":[{"id":b["id"],"name":b["value"]["name"],"day":b["value"]["diary_date"],"before":a["value"]["totals"]["calories"],"after":b["value"]["totals"]["calories"]} for b,a in zip(before,after)],
                  "explanation":"Restore the saved allowances only if every affected meal still has the reviewed revision."}
        reviewed = hashlib.sha256(packed({"operation":"bulk_undo","record":record["operation_id"],"before":before,"after":after}).encode()).hexdigest()
        return await self._strict(context,identity,digest,changes,report,reviewed,time.monotonic()+self.controller.config.nutrition.bulk_preview_lifetime_s,policy(self.controller.config),record["operation_id"])
