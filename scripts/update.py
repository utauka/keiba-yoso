"""1回分の更新。GitHub Actions から10分ごと（週末）に呼ばれます。

  python scripts/update.py            … ふだんの更新（必要なものだけ取りに行く）
  python scripts/update.py --prep     … 前日の準備（重賞さがし・出馬表・過去成績・前売りオッズ）
  python scripts/update.py --dates 20261018 … 日付を決めて試す

流れ
  1. 今日から3日先までの重賞（G1〜G3）をさがす（6時間に1回）
  2. 出馬表を取る。発走100分前からは、馬体重が出るまで毎回取り直す
  3. 出走馬の過去成績から能力指数の材料を作る（レースごとに1回）
  4. 単勝と3連複のオッズを取り、予想と買い目を作り直す
  5. 発走時刻を過ぎたら、その時点の買い目で確定。10分後から結果を取りに行き、答え合わせ
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import netkeiba as nk  # noqa: E402
from common import (DATA, JST, all_race_files, iso, load_config, now_jst, parse_iso,  # noqa: E402
                    race_file, read_json, write_json)
from model import default_picks, horse_features, predict, settle  # noqa: E402

SCHEDULE = DATA / "schedule.json"


def post_time(r: dict) -> dt.datetime | None:
    if not r.get("time"):
        return None
    d = r["date"]
    hh, mm = map(int, r["time"].split(":"))
    return dt.datetime(int(d[:4]), int(d[4:6]), int(d[6:]), hh, mm, tzinfo=JST)


# ------------------------------------------------------------------ 1. 重賞さがし
def discover(now: dt.datetime, force: bool, dates: list[str] | None, cfg: dict) -> dict:
    sch = read_json(SCHEDULE, {"checked": None, "dates": {}})
    last = parse_iso(sch.get("checked"))
    if not force and not dates and last and (now - last).total_seconds() < 6 * 3600:
        return sch
    days = dates or [(now + dt.timedelta(days=i)).strftime("%Y%m%d") for i in range(0, 4)]
    for d in days:
        lst = nk.race_list(d)
        graded = [r for r in lst if r.get("grade") in cfg["grades"] and r.get("surface") != "障"]
        if lst:
            sch["dates"][d] = graded
            print(f"{d}: {len(lst)}レース中 重賞{len(graded)} " + " ".join(f"{r['venue']}{r['rno']}R {r['name']}({r['grade']})" for r in graded))
        elif d in sch["dates"] and not sch["dates"][d]:
            pass
    # 古い日付は消す（2週間より前）
    cut = (now - dt.timedelta(days=14)).strftime("%Y%m%d")
    sch["dates"] = {d: v for d, v in sorted(sch["dates"].items()) if d >= cut}
    sch["checked"] = iso(now)
    write_json(SCHEDULE, sch)
    return sch


# ------------------------------------------------------------------ 2〜5. レースごと
def merge_entries(race: dict, info: dict) -> None:
    old = {h["horse_id"]: h for h in race.get("horses", [])}
    horses = []
    for h in info["horses"]:
        o = old.get(h["horse_id"], {})
        for k in ("feat", "odds_hist"):
            if k in o:
                h[k] = o[k]
        if "weight" not in h and "weight" in o:  # 一時的に読めなかったときは前の値を残す
            h["weight"], h["weight_diff"] = o["weight"], o.get("weight_diff")
        horses.append(h)
    race["horses"] = horses
    for k in ("name", "grade", "time", "surface", "distance", "turn", "weather", "going", "data2"):
        if info.get(k):
            race[k] = info[k]


def update_race(meta: dict, now: dt.datetime, cfg: dict, prep: bool) -> bool:
    path = race_file(meta["race_id"])
    race = read_json(path) or {**meta}
    if race.get("result") and race.get("settle"):
        return False
    for k in ("venue", "rno", "date"):
        race.setdefault(k, meta.get(k))
    post = post_time(race) or post_time(meta)
    mins = (post - now).total_seconds() / 60 if post else None
    today = race["date"] == now.strftime("%Y%m%d")
    changed = False

    # 2. 出馬表
    ent_at = parse_iso(race.get("entries_at"))
    age = (now - ent_at).total_seconds() / 60 if ent_at else 1e9
    weights_in = race.get("horses") and all(h.get("weight") for h in race["horses"] if not h.get("scratched"))
    need = (not race.get("horses") or age > 360 or prep
            or (today and mins is not None and 0 < mins <= 100 and (not weights_in or age > 25)))
    if need and (mins is None or mins > -5):
        info = nk.shutuba(race["race_id"])
        if info and info.get("horses"):
            merge_entries(race, info)
            race["entries_at"] = iso(now)
            changed = True
            post = post_time(race) or post
            mins = (post - now).total_seconds() / 60 if post else None

    # 3. 能力の材料（過去成績）
    for h in race.get("horses", []):
        if h.get("feat") is None or prep and not h.get("feat_at"):
            hist = nk.horse_results(h["horse_id"])
            if hist is not None:
                h["feat"] = horse_features(hist, race)
                h["feat_at"] = iso(now)
                changed = True

    # 4. オッズと予想（発走前だけ）
    if mins is not None and mins > 0:
        odds_at = parse_iso(race.get("odds_at"))
        oage = (now - odds_at).total_seconds() / 60 if odds_at else 1e9
        if today or prep or oage > 180:
            win = nk.win_odds(race["race_id"])
            trio = nk.trio_odds(race["race_id"])
            race["odds_at"] = iso(now)
            race["trio_source"] = trio.get("source")
            byu = {h.get("umaban"): h for h in race.get("horses", [])}
            for u, (o, _) in (win.get("odds") or {}).items():
                h = byu.get(u)
                if h is not None:
                    hist = h.setdefault("odds_hist", [])
                    if not hist or hist[-1][1] != o or round(mins) < hist[-1][0] - 30:
                        hist.append([round(mins), o])
                        del hist[:-40]
            pred = predict(race, win, trio, cfg, now, post)
            if pred.get("ok"):
                race["pred"] = pred
                race["picks"] = default_picks(pred, cfg)
            else:
                race["pred_note"] = pred.get("reason")
            changed = True
    elif mins is not None and mins <= 0 and race.get("pred") and not race.get("final"):
        race["final"] = True       # 発走時刻の時点の買い目で確定
        changed = True

    # 5. 結果
    if mins is not None and mins <= -10 and not race.get("result"):
        res = nk.result(race["race_id"])
        if res:
            race["result"] = res
            race["final"] = True
            race["settle"] = settle(race.get("picks") or [], res, cfg["unit"])
            changed = True
            s = race["settle"]
            print(f"  結果 {race['name']}: 3連複 {res['trio']} → 払戻 {s['return']}円 / {s['stake']}円")

    if changed:
        write_json(path, race, compact=True)
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prep", action="store_true")
    ap.add_argument("--dates", nargs="*")
    ap.add_argument("--now", help="試験用：この時刻として動かす（例 2026-10-18T15:00）")
    a = ap.parse_args()
    cfg = load_config()
    now = dt.datetime.fromisoformat(a.now).replace(tzinfo=JST) if a.now else now_jst()
    sch = discover(now, a.prep, a.dates, cfg)

    lo = (now - dt.timedelta(days=3)).strftime("%Y%m%d")
    hi = (now + dt.timedelta(days=3)).strftime("%Y%m%d")
    targets = [r for d, rs in sch["dates"].items() if lo <= d <= hi for r in rs]
    # 結果待ちのまま残っているレースも拾う
    known = {r["race_id"] for r in targets}
    for p in all_race_files():
        r = read_json(p) or {}
        if r.get("race_id") not in known and not r.get("result") and r.get("date", "") >= lo:
            targets.append({k: r.get(k) for k in ("race_id", "date", "venue", "rno", "name", "grade", "time")})

    changed = False
    for meta in sorted(targets, key=lambda r: (r["date"], r.get("time") or "")):
        print(f"- {meta['date']} {meta.get('venue')}{meta.get('rno')}R {meta.get('name')}")
        try:
            changed |= update_race(meta, now, cfg, a.prep)
        except Exception as e:  # 1レースの失敗で全体を止めない
            import traceback
            traceback.print_exc()
            print(f"  ! このレースは今回スキップ: {e}")
    print(f"通信 {nk.REQUEST_COUNT[0]} 回 / 変更 {'あり' if changed else 'なし'}")

    from build_site import build
    build(now)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")


if __name__ == "__main__":
    main()
