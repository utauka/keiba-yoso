"""画面のひな形 site/template.html にデータを埋めこんで site/index.html を作る。"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from common import SITE, all_race_files, load_config, now_jst, read_json  # noqa: E402

KEEP = ("race_id", "date", "venue", "rno", "name", "grade", "time", "surface", "distance", "turn",
        "weather", "going", "final", "odds_at", "entries_at", "trio_source", "pred_note")


def slim(r: dict, full: bool) -> dict:
    o = {k: r.get(k) for k in KEEP if r.get(k) is not None}
    p = r.get("pred")
    if p:
        if full:
            o["pred"] = p
        else:
            o["pred"] = {"cands": p.get("cands"), "generated": p.get("generated")}
            o["marks"] = [[h["u"], h["mark"], h.get("name")] for h in p.get("horses", []) if h.get("mark")]
    if r.get("picks"):
        o["picks"] = r["picks"]
    if r.get("result"):
        o["result"] = r["result"]
        o["settle"] = r.get("settle")
    return o


def build(now: dt.datetime | None = None) -> None:
    now = now or now_jst()
    cfg = load_config()
    today = now.strftime("%Y%m%d")
    week_ago = (now - dt.timedelta(days=4)).strftime("%Y%m%d")
    races = [read_json(p) for p in all_race_files()]
    races = [r for r in races if r and r.get("race_id")]
    races.sort(key=lambda r: (r["date"], r.get("time") or ""))
    current = [slim(r, True) for r in races if r["date"] >= week_ago and (not r.get("result") or r["date"] >= week_ago)]
    history = [slim(r, False) for r in races if r.get("result")]
    history.reverse()
    data = {
        "generated": now.isoformat(timespec="minutes"), "today": today,
        "cfg": {"budget": cfg["budget"], "unit": cfg["unit"], "bands": cfg["bands"],
                "alloc": cfg["picks"]["default_alloc"]},
        "current": current, "history": history,
    }
    tpl = (SITE / "template.html").read_text(encoding="utf-8")
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = tpl.replace("<!--DATA-->", f'<script type="application/json" id="data">{blob}</script>')
    (SITE / "index.html").write_text(page, encoding="utf-8")
    print(f"ページを作りました（今週 {len(current)} レース / 成績 {len(history)} レース）")


if __name__ == "__main__":
    build()
