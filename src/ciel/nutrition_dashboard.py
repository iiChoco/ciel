"""The food log over time, with its missing days left visible.

**Intake is not a deficit.** The old page could show one diary day but could
not distinguish a week of complete readings from a week with missing meals.
This reader keeps partial intake visible, uses effective-dated owner settings,
and includes only completed, usable days in estimated deficit statistics.
A target never supplies expenditure; cumulative calories never claim fat loss.

**Every number has evidence.** One worker read produces the series and review
from saved meal snapshots, excluding plans and drafts. Macro subtotals disclose
coverage, averages name their eligible days, and review entries link to meals.
Bounded ranges refuse excess data instead of silently omitting it.

**Weight is an observation.** Optional manual daily readings retain entered
units. A trailing calendar-window mean uses observed readings only, with no
interpolation across missing measurements and no automatic target changes.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import json
from typing import Any

from ciel.nutrition import NUTRIENTS, NutritionController, NutritionStore, OwnerContext, diary_day, occurrence


def validate_limits(config: Any) -> None:
    for name, maximum in (("dashboard_max_days",366),("dashboard_max_meals",10000),("dashboard_review_limit",25),("weight_trend_days",90)):
        value = getattr(config,name)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"{name} must be an integer between 1 and {maximum}")


def dates(start: str, end: str) -> list[str]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return [(first+timedelta(days=i)).isoformat() for i in range((last-first).days+1)]


def aggregate(day: str, meals: list[dict[str, Any]], completion: dict[str, Any], settings: dict[str, Any], today: str) -> dict[str, Any]:
    complete = completion.get("complete") is True
    zero = complete and completion.get("zero_intake") is True and not meals
    usable = bool(meals) or zero
    totals, coverage = {}, {}
    for key in NUTRIENTS:
        known = [m["totals"][key] for m in meals if m["totals"].get(key) is not None]
        totals[key] = round(sum(known),2) if known else (0.0 if zero else None)
        coverage[key] = {"known_items":sum(m["coverage"][key] for m in meals),"total_items":sum(len(m["items"]) for m in meals)}
    status = "complete" if complete and usable else "empty_without_zero" if complete else "incomplete" if meals or completion else "missing"
    calories_usable = usable and totals["calories"] is not None and coverage["calories"]["known_items"] == coverage["calories"]["total_items"]
    reason = "future" if day > today else "not_complete" if not complete else "intake_unknown" if not calories_usable else "expenditure_unset" if settings.get("expenditure") is None else None
    deficit = round(settings["expenditure"]-totals["calories"],2) if reason is None else None
    return {"day":day,"status":status,"complete":complete,"zero_intake":zero,"totals":totals,"coverage":coverage,
            "meal_count":len(meals),"allowance":round(sum(m["allowance"] for m in meals),2) if usable else None,
            "settings":settings,"deficit":deficit,"deficit_exclusion":reason}


def averages(rows: list[dict[str, Any]], today: str) -> dict[str, Any]:
    result = {}
    for key in NUTRIENTS:
        eligible = [r for r in rows if r["day"] <= today and r["status"] == "complete" and r["totals"][key] is not None
                    and r["coverage"][key]["known_items"] == r["coverage"][key]["total_items"]]
        result[key] = {"value":round(sum(r["totals"][key] for r in eligible)/len(eligible),2) if eligible else None,"days":len(eligible)}
    deficits = [r["deficit"] for r in rows if r["deficit"] is not None]
    result["deficit"] = {"value":round(sum(deficits)/len(deficits),2) if deficits else None,"days":len(deficits)}
    return result


class NutritionDashboard:
    def __init__(self, controller: NutritionController) -> None:
        self.controller = controller

    async def read(self, context: OwnerContext, args: dict[str, Any]) -> dict[str, Any]:
        c = self.controller
        store = c._admit(context)
        validate_limits(c.config.nutrition)
        today = occurrence({},c.config)["diary_date"]
        end = diary_day(args.get("to",today))
        if date.fromisoformat(end).toordinal() < 7:
            raise ValueError("Choose an ending date with room for its seven-day review.")
        start = diary_day(args.get("from",(date.fromisoformat(end)-timedelta(days=min(7,c.config.nutrition.dashboard_max_days)-1)).isoformat()))
        if date.fromisoformat(start).toordinal() < c.config.nutrition.weight_trend_days:
            raise ValueError("Choose a starting date with room for the weight trend window.")
        length = (date.fromisoformat(end)-date.fromisoformat(start)).days+1
        if not 1 <= length <= c.config.nutrition.dashboard_max_days:
            raise ValueError(f"Choose a range of 1–{c.config.nutrition.dashboard_max_days} diary days.")
        return await c._read(context,self._read,store,start,end,today)

    def _read(self, store: NutritionStore, start: str, end: str, today: str) -> dict[str, Any]:
        n = self.controller.config.nutrition
        db = store._connection()
        weekly_start = (date.fromisoformat(end)-timedelta(days=6)).isoformat()
        first = min(start,weekly_start)
        meal_rows = db.execute("SELECT id,revision,body FROM records WHERE kind='meal' AND body IS NOT NULL AND json_extract(body,'$.diary_date') BETWEEN ? AND ? ORDER BY json_extract(body,'$.diary_date'),id LIMIT ?",(first,end,n.dashboard_max_meals+1)).fetchall()
        if len(meal_rows) > n.dashboard_max_meals:
            raise ValueError("This dashboard range contains too many meals; choose a shorter range. No totals were omitted.")
        meals: dict[str,list[dict[str,Any]]] = defaultdict(list)
        for row in meal_rows:
            value = json.loads(row["body"])
            meals[value["diary_date"]].append({"id":row["id"],"revision":row["revision"],**value})
        completions = {r["id"]:json.loads(r["body"]) for r in db.execute("SELECT id,body FROM records WHERE kind='day' AND body IS NOT NULL AND id BETWEEN ? AND ?",(first,end))}
        previous = db.execute("SELECT body FROM records WHERE kind='settings' AND body IS NOT NULL AND id<? ORDER BY id DESC LIMIT 1",(first,)).fetchone()
        current_settings = json.loads(previous["body"]) if previous else {}
        settings = {r["id"]:json.loads(r["body"]) for r in db.execute("SELECT id,body FROM records WHERE kind='settings' AND body IS NOT NULL AND id BETWEEN ? AND ? ORDER BY id",(first,end))}
        all_days = []
        for day in dates(first,end):
            current_settings = settings.get(day,current_settings)
            all_days.append(aggregate(day,meals[day],completions.get(day,{}),current_settings,today))
        rows = [r for r in all_days if r["day"] >= start]
        subtotal, count = 0.0, 0
        for row in rows:
            if row["deficit"] is not None:
                subtotal = round(subtotal+row["deficit"],2); count += 1
                row["cumulative_deficit"] = subtotal
            else:
                row["cumulative_deficit"] = None
            row["included_days"] = count
        weight_first = (date.fromisoformat(start)-timedelta(days=n.weight_trend_days-1)).isoformat()
        weights = {r["id"]:{"id":r["id"],"revision":r["revision"],"value":json.loads(r["body"]) if r["body"] else None}
                   for r in db.execute("SELECT id,revision,body FROM records WHERE kind='weight' AND id BETWEEN ? AND ? ORDER BY id",(weight_first,end))}
        for row in rows:
            record = weights.get(row["day"],{"id":row["day"],"revision":0,"value":None})
            since = (date.fromisoformat(row["day"])-timedelta(days=n.weight_trend_days-1)).isoformat()
            readings = [w["value"]["kg"] for day,w in weights.items() if since <= day <= row["day"] and w["value"]]
            row["weight"] = record
            row["weight_trend"] = {"kg":round(sum(readings)/len(readings),4) if record["value"] and len(readings)>=2 else None,"readings":len(readings) if record["value"] else 0}
        weekly_rows = [r for r in all_days if r["day"] >= weekly_start]
        weekly_meals = [m for day,entries in meals.items() if weekly_start <= day <= end for m in entries]
        groups: dict[str,list[dict[str,Any]]] = defaultdict(list)
        for m in weekly_meals:
            groups[m["name"].strip().casefold()].append(m)
        limit = n.dashboard_review_limit
        def evidence(m: dict[str,Any]) -> dict[str,Any]:
            missing = [key for key in NUTRIENTS if m["coverage"][key] < len(m["items"])]
            return {"id":m["id"],"revision":m["revision"],"day":m["diary_date"],"name":m["name"],"calories":m["totals"]["calories"],"allowance":m["allowance"],"missing_nutrients":missing}
        recurring = sorted((v for v in groups.values() if len(v)>1),key=lambda v:(-len(v),v[0]["name"].casefold()))
        high = sorted((m for m in weekly_meals if m["allowance"]>0),key=lambda m:(-m["allowance"],m["diary_date"],m["id"]))
        unknown = sorted((m for m in weekly_meals if any(m["coverage"][k]<len(m["items"]) for k in NUTRIENTS)),key=lambda m:(m["diary_date"],m["id"]))
        review = {"from":weekly_start,"to":end,"days":7,"complete_days":sum(r["status"]=="complete" and r["day"]<=today for r in weekly_rows),
                  "averages":averages(weekly_rows,today),"meal_count":len(weekly_meals),"limit":limit,
                  "recurring":{"total":len(recurring),"entries":[{"name":v[0]["name"],"count":len(v),"examples":[evidence(m) for m in v[:limit]]} for v in recurring[:limit]]},
                  "allowances":{"total":len(high),"entries":[evidence(m) for m in high[:limit]]},
                  "unresolved":{"total":len(unknown),"entries":[evidence(m) for m in unknown[:limit]]}}
        return {"from":start,"to":end,"today":today,"days":rows,"averages":averages(rows,today),"weekly_review":review,
                "summary":{"days":len(rows),"complete_days":sum(r["status"]=="complete" and r["day"]<=today for r in rows),"deficit_days":count,
                           "cumulative_deficit":subtotal if count else None,"gap_days":len(rows)-count},
                "weight_trend_days":n.weight_trend_days,"max_days":n.dashboard_max_days,
                "explanation":"Estimated expenditure minus logged calories, including their visible allowance. Only completed days with usable intake and owner-supplied expenditure contribute. Gaps are excluded; this subtotal is not measured fat loss."}
