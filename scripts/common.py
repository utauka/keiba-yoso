"""共通部品：設定の読み込み、時刻、保存と読み込み。"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"                 # 集めたデータ（レースごとの小さなファイル）
RACES = DATA / "races"
SITE = ROOT / "site"
SITE_DATA = SITE / "data"            # 画面が読むデータ
DEBUG = DATA / "debug"               # 読み取りに失敗したときの手がかり

JST = dt.timezone(dt.timedelta(hours=9))

# JRAの競馬場コード（race_id の5〜6けた目）
VENUES = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉",
}
VENUE_CODE = {v: k for k, v in VENUES.items()}


def load_config() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def now_jst() -> dt.datetime:
    return dt.datetime.now(JST)


def iso(t: dt.datetime | None) -> str | None:
    return t.astimezone(JST).isoformat(timespec="minutes") if t else None


def parse_iso(s: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(s) if s else None


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, obj, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        txt = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    else:
        txt = json.dumps(obj, ensure_ascii=False, indent=1)
    path.write_text(txt + "\n", encoding="utf-8")


def race_file(race_id: str) -> Path:
    return RACES / race_id[:4] / f"{race_id}.json"


def all_race_files():
    return sorted(RACES.glob("*/*.json"))
