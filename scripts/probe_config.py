"""Probe the configuration loader — defaults, the file, the environment.

    uv run --no-sync python scripts/probe_config.py

Every path here is explicit and temporary; nothing under ``~/.ciel`` is
read, and the process's own ``CIEL_*`` variables are set aside for the
run. Pins: an absent file is the defaults; a TOML section's values land on
the section, coerced to the field's type; the environment wins over the
file; a boolean accepts its positive and negative spellings from either
source; a spelling outside both sets is refused with the section and field
named, never read as false (``CIEL_CONFIRM_ASK_FIRST=ture`` once turned
the confirmation gate off); an integer that is not one is refused the same
way; an unknown key is named in the log and ignored; and the two top-level
fields honour their own variables.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ciel.config import Config, load_config

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


def environment(**variables: str) -> dict[str, str]:
    """This process's environment with every CIEL_* variable set aside, plus the given ones."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("CIEL_")}
    return {**clean, **variables}


def refused(path: Path, **variables: str) -> str:
    with patch.dict(os.environ, environment(**variables), clear=True):
        try:
            load_config(path)
        except ValueError as exc:
            return str(exc)
    return ""


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ciel-config-probe-") as tmp:
        root = Path(tmp)
        absent = root / "absent.toml"
        print("defaults, the file, the environment")
        with patch.dict(os.environ, environment(), clear=True):
            check("an absent file is the defaults, and the gate asks first", load_config(absent) == Config() and Config().confirm.ask_first is True)
            written = root / "config.toml"
            written.write_text("[confirm]\nask_first = false\n\n[hub]\nconfirm_timeout_s = 12.5\n\n[web]\nhistory_lines = 7\n", encoding="utf-8")
            loaded = load_config(written)
            check("a TOML section's values land on the section, coerced to the field's type",
                  loaded.confirm.ask_first is False and loaded.hub.confirm_timeout_s == 12.5 and loaded.web.history_lines == 7)
        with patch.dict(os.environ, environment(CIEL_CONFIRM_ASK_FIRST="yes", CIEL_WEB_HISTORY_LINES="9"), clear=True):
            loaded = load_config(written)
            check("the environment wins over the file", loaded.confirm.ask_first is True and loaded.web.history_lines == 9)

        print("\nwhat a boolean accepts")
        for word in ("1", "true", "Yes", "ON", " true "):
            with patch.dict(os.environ, environment(CIEL_CONFIRM_ASK_FIRST=word), clear=True):
                check(f"{word.strip()!r} is true", load_config(absent).confirm.ask_first is True)
        for word in ("0", "false", "No", "OFF"):
            with patch.dict(os.environ, environment(CIEL_CONFIRM_ASK_FIRST=word), clear=True):
                check(f"{word!r} is false", load_config(absent).confirm.ask_first is False)
        message = refused(absent, CIEL_CONFIRM_ASK_FIRST="ture")
        check("a spelling outside both sets is refused with the section and field named, never read as false",
              "[confirm].ask_first" in message and "'ture'" in message and "true/false" in message)
        written.write_text("[confirm]\nask_first = 'flase'\n", encoding="utf-8")
        message = refused(written)
        check("from the file as well", "[confirm].ask_first" in message and "'flase'" in message)
        message = refused(absent, CIEL_WEB_HISTORY_LINES="many")
        check("an integer that is not one is refused the same way", "[web].history_lines" in message and "many" in message)

        print("\nwhat is named and ignored")
        written.write_text("[confirm]\nask_frist = false\n", encoding="utf-8")
        with patch.dict(os.environ, environment(), clear=True), patch("ciel.config.log") as log:
            loaded = load_config(written)
        check("an unknown key is named in the log and changes nothing",
              loaded.confirm.ask_first is True and any("ask_frist" in str(call) for call in log.warning.call_args_list))

        print("\nthe two top-level fields")
        with patch.dict(os.environ, environment(CIEL_LOG_LEVEL="DEBUG", CIEL_STATE_DIR=str(root / "state")), clear=True):
            loaded = load_config(absent)
        check("CIEL_LOG_LEVEL and CIEL_STATE_DIR are honoured", loaded.log_level == "DEBUG" and loaded.state_dir == root / "state")

    with patch.dict(os.environ, environment(CIEL_NUTRITION_ENABLED="true", CIEL_NUTRITION_DIARY_DAY_START_HOUR="5"), clear=True):
        loaded = load_config(absent)
    check("nutrition is opt-in and its diary cutoff can be configured", not Config().nutrition.enabled and loaded.nutrition.enabled and loaded.nutrition.diary_day_start_hour == 5)
    with patch.dict(os.environ, environment(CIEL_NUTRITION_STATE_DIR=str(root / "custom-food-state")), clear=True):
        loaded = load_config(absent)
    check("a configured nutrition directory is a path", loaded.nutrition.state_dir == root / "custom-food-state")
    with patch.dict(os.environ, environment(CIEL_NUTRITION_PHOTO_ANALYSIS="false", CIEL_NUTRITION_PHOTO_MAX_BYTES="1000000", CIEL_NUTRITION_PHOTO_MAX_BUDGET_USD="0.10"), clear=True):
        loaded = load_config(absent)
    check("photo analysis, byte bounds, and budget use the typed configuration loader", not loaded.nutrition.photo_analysis and loaded.nutrition.photo_max_bytes == 1000000 and loaded.nutrition.photo_max_budget_usd == .10)
    check("a misspelled photo analysis switch is refused", "[nutrition].photo_analysis" in refused(absent, CIEL_NUTRITION_PHOTO_ANALYSIS="flase"))
    with patch.dict(os.environ, environment(CIEL_NUTRITION_BULK_MAX_MEALS="25", CIEL_NUTRITION_BULK_PREVIEW_LIFETIME_S="90", CIEL_NUTRITION_MAX_LIBRARY_RECORDS="200"), clear=True):
        loaded = load_config(absent)
    check("library capacity and historical review bounds are typed configuration", loaded.nutrition.bulk_max_meals==25 and loaded.nutrition.bulk_preview_lifetime_s==90 and loaded.nutrition.max_library_records==200)
    with patch.dict(os.environ, environment(CIEL_NUTRITION_DASHBOARD_MAX_DAYS="90", CIEL_NUTRITION_DASHBOARD_MAX_MEALS="1000", CIEL_NUTRITION_DASHBOARD_REVIEW_LIMIT="5", CIEL_NUTRITION_WEIGHT_TREND_DAYS="14"), clear=True):
        loaded = load_config(absent)
    check("dashboard ranges evidence bounds and weight windows use typed configuration", (loaded.nutrition.dashboard_max_days,loaded.nutrition.dashboard_max_meals,loaded.nutrition.dashboard_review_limit,loaded.nutrition.weight_trend_days)==(90,1000,5,14))
    print(f"\nall {len(CHECKS)} checks passed")


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    main()
