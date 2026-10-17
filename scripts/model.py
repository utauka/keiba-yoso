"""予想のしくみ（中学生にもわかる説明は site のしくみタブと README にあります）。

1. 能力指数   … 過去の走り（着順・勝ち馬との差・レースの格）から、その馬の力を数字にする
2. パドック指標 … 当日の馬体重の増減と、パドックの時間帯のオッズの動き
3. 勝つ確率   … 単勝オッズ（みんなの評価）を土台に、能力指数とパドック指標で少し動かす
4. 3連複の確率 … 勝つ確率から「この3頭が3着以内に入る確率」を全部の組み合わせで計算
5. 買い目     … 3連複のオッズで 本命／中穴／大穴 に分け、それぞれで良い組み合わせを選ぶ
"""
from __future__ import annotations

import datetime as dt
import itertools
import math

CLASS_WEIGHT = {"G1": 1.15, "G2": 1.10, "G3": 1.06, "L": 1.02, "OP": 1.00, "3勝": 0.92,
                "2勝": 0.86, "1勝": 0.80, "未勝利": 0.74, "新馬": 0.74, None: 0.95}
RECENCY = [1.0, 0.85, 0.72, 0.61, 0.52]


# ------------------------------------------------------------------ 1. 能力
def run_quality(r: dict) -> float | None:
    rank, heads = r.get("rank"), r.get("heads")
    if not rank or not heads:
        return 0.15 if r.get("heads") else None   # 競走中止など
    pos = 1 - (rank - 1) / max(heads - 1, 1)
    m = r.get("margin")
    if m is None:
        m = 0.0 if rank == 1 else 1.0
    m = min(max(m, 0.0), 2.5)
    q = 0.55 * pos + 0.45 * (1 - m / 2.5)
    return q * CLASS_WEIGHT.get(r.get("grade"), 0.95)


def horse_features(history: list[dict], race: dict) -> dict:
    date = race["date"]
    d0 = f"{date[:4]}-{date[4:6]}-{date[6:]}" if "-" not in date else date
    runs = [r for r in history if r.get("date", "9") < d0]
    runs.sort(key=lambda r: r["date"], reverse=True)
    qs = [(r, run_quality(r)) for r in runs]
    qs = [(r, q) for r, q in qs if q is not None]
    f: dict = {"runs": len(qs)}
    if not qs:
        f.update(form=None, dist=None, course=None, g_top3=0, surf_runs=0, days_off=None, best_weight=None)
        return f
    w = RECENCY[:len(qs[:5])]
    f["form"] = round(sum(q * k for (_, q), k in zip(qs[:5], w)) / sum(w), 4)
    sur, dist, ven = race.get("surface"), race.get("distance"), race.get("venue")
    same = sorted([q for r, q in qs if r.get("surface") == sur and dist and r.get("distance")
                   and abs(r["distance"] - dist) <= 200], reverse=True)
    f["dist"] = round(sum(same[:2]) / len(same[:2]), 4) if same else None
    crs = [q for r, q in qs if r.get("venue") == ven and r.get("surface") == sur]
    f["course"] = round(sum(crs) / len(crs), 4) if crs else None
    f["g_top3"] = sum(1 for r, _ in qs if r.get("grade") in ("G1", "G2", "G3") and r.get("rank") and r["rank"] <= 3)
    f["g_win"] = sum(1 for r, _ in qs if r.get("grade") in ("G1", "G2", "G3") and r.get("rank") == 1)
    f["surf_runs"] = sum(1 for r, _ in qs if r.get("surface") == sur)
    try:
        last = dt.date.fromisoformat(qs[0][0]["date"])
        f["days_off"] = (dt.date.fromisoformat(d0) - last).days
    except ValueError:
        f["days_off"] = None
    good = [r["weight"] for r, _ in qs[:10] if r.get("weight") and r.get("rank") and r["rank"] <= 3]
    f["best_weight"] = round(sum(good) / len(good)) if good else next(
        (r["weight"] for r, _ in qs if r.get("weight")), None)
    f["last5"] = [r.get("rank") for r, _ in qs[:5]]
    return f


def ability_raw(f: dict) -> float:
    if not f or not f.get("runs"):
        return 0.55
    form = f["form"]
    dist = f["dist"] if f.get("dist") is not None else form * 0.92
    course = f["course"] if f.get("course") is not None else form * 0.95
    g = 0.4 + 0.6 * min(f.get("g_top3", 0), 4) / 4
    v = 0.42 * form + 0.28 * dist + 0.12 * course + 0.18 * g
    if f.get("surf_runs", 0) == 0:
        v -= 0.06          # 初めての芝／ダート
    d = f.get("days_off")
    if d is not None and d > 365:
        v -= 0.08          # 1年以上の休み明け
    elif d is not None and d > 180:
        v -= 0.04          # 半年以上の休み明け
    return v


# ------------------------------------------------------------------ 2. パドック指標
def paddock(h: dict, minutes_to_post: float | None) -> tuple[float, list[str]]:
    score, notes = 0.0, []
    wd, wt, bw = h.get("weight_diff"), h.get("weight"), (h.get("feat") or {}).get("best_weight")
    if wt:
        if wd is not None:
            a = abs(wd)
            if a >= 14:
                score -= 0.25
                notes.append(f"体重{wd:+d}kg 大きく増減")
            elif a >= 10:
                score -= 0.15
                notes.append(f"体重{wd:+d}kg やや大きい")
            elif a >= 8:
                score -= 0.06
        if bw:
            g = wt - bw
            if abs(g) <= 4:
                score += 0.08
                notes.append("好走時の体重に近い")
            elif abs(g) >= 16:
                score -= 0.08
                notes.append(f"好走時より{g:+d}kg")
    snaps = h.get("odds_hist") or []
    if minutes_to_post is not None and minutes_to_post <= 75 and len(snaps) >= 2:
        base = None
        for t, o in snaps:  # パドック前（発走75分前より前）の最後のオッズを基準にする
            if t >= 75:
                base = o
        base = base or snaps[0][1]
        cur = snaps[-1][1]
        if base and cur:
            ratio = cur / base
            if ratio <= 0.65:
                score += 0.22
                notes.append(f"単勝{base:g}→{cur:g} 急に人気上昇")
            elif ratio <= 0.8:
                score += 0.12
                notes.append(f"単勝{base:g}→{cur:g} 人気上昇")
            elif ratio >= 1.6:
                score -= 0.18
                notes.append(f"単勝{base:g}→{cur:g} 急に人気低下")
            elif ratio >= 1.3:
                score -= 0.09
                notes.append(f"単勝{base:g}→{cur:g} 人気低下")
    return score, notes


# ------------------------------------------------------------------ 3〜4. 確率
def softmax(xs):
    m = max(xs)
    e = [math.exp(x - m) for x in xs]
    s = sum(e)
    return [x / s for x in e]


def trio_probs(p: list[float], l2: float, l3: float) -> dict:
    """勝つ確率から、3頭の組み合わせ（順番なし）で3着以内に入る確率を出す。
    2着・3着は勝つ確率ほど差がつかないので、少しならして計算する（Lo・Bacon-Shone の方法）。"""
    n = len(p)
    p2 = [x ** l2 for x in p]
    p3 = [x ** l3 for x in p]
    s2, s3 = sum(p2), sum(p3)
    out: dict = {}
    for i in range(n):
        if p[i] <= 0:
            continue
        for j in range(n):
            if j == i:
                continue
            pj = p2[j] / (s2 - p2[i])
            for k in range(n):
                if k == i or k == j:
                    continue
                pk = p3[k] / (s3 - p3[i] - p3[j])
                key = tuple(sorted((i, j, k)))
                out[key] = out.get(key, 0.0) + p[i] * pj * pk
    return out


def band_of(odds: float, cfg: dict) -> str:
    b = cfg["bands"]
    if odds < b["honmei_max"]:
        return "honmei"
    if odds < b["chuana_max"]:
        return "chuana"
    return "oana"


def predict(race: dict, win: dict, trio: dict, cfg: dict, now: dt.datetime, post: dt.datetime | None) -> dict:
    m = cfg["model"]
    horses = [h for h in race["horses"] if not h.get("scratched") and h.get("umaban")]
    n = len(horses)
    if n < 3:
        return {"ok": False, "reason": "出走馬が3頭未満か、馬番が未確定です"}
    mins = (post - now).total_seconds() / 60 if post else None

    # 能力指数（レース内で平均50・ばらつき10にそろえる）
    raw = [ability_raw(h.get("feat") or {}) for h in horses]
    mu = sum(raw) / n
    sd = (sum((x - mu) ** 2 for x in raw) / n) ** 0.5 or 1.0
    z = [(x - mu) / sd for x in raw]

    pad = [paddock(h, mins) for h in horses]
    wo = win.get("odds") or {}
    have = [h["umaban"] in wo for h in horses]
    use_market = sum(have) >= max(3, int(0.7 * n))
    if use_market:
        inv = [(1 / wo[h["umaban"]][0]) ** m["market_sharpen"] if h["umaban"] in wo else None for h in horses]
        lo = min(x for x in inv if x)
        inv = [x if x else lo * 0.5 for x in inv]
        tot = sum(inv)
        pm = [x / tot for x in inv]
        s = [math.log(pm[i]) + m["ability_weight"] * z[i] + pad[i][0] for i in range(n)]
    else:
        pm = [1 / n] * n
        s = [m["ability_weight_no_market"] * z[i] + pad[i][0] for i in range(n)]
    p = softmax(s)

    tp = trio_probs(p, m["lambda2"], m["lambda3"])
    tpm = trio_probs(pm, m["lambda2"], m["lambda3"]) if use_market else None
    real = trio.get("odds") or {}
    ub = [h["umaban"] for h in horses]
    keyof = {key: tuple(sorted(ub[x] for x in key)) for key in tp}
    if real and sum(1 for key in tp if keyof[key] in real) >= 0.8 * len(tp):
        # 3連複の売れ方（みんなの評価）とも混ぜる。単勝だけから計算した確率は、
        # 人気のない馬の組み合わせを高く見すぎることがあるため
        a = m["trio_model_weight"]
        inv = {key: 1 / real[keyof[key]] for key in tp if keyof[key] in real}
        lo = min(inv.values())
        tot = sum(inv.values()) + lo * 0.5 * (len(tp) - len(inv))
        mix = {key: math.exp(a * math.log(v) + (1 - a) * math.log(inv.get(key, lo * 0.5) / tot))
               for key, v in tp.items() if v > 0}
        z_ = sum(mix.values())
        tp = {key: v / z_ for key, v in mix.items()}
        tq = {key: inv.get(key, lo * 0.5) / tot for key in tp}
        if tpm is not None:
            tpm = tq          # 「みんなの評価」の3着内率も3連複の売れ方から出す
    top3 = [0.0] * n
    for (i, j, k), v in tp.items():
        top3[i] += v
        top3[j] += v
        top3[k] += v
    top3m = [0.0] * n
    if tpm:
        for (i, j, k), v in tpm.items():
            top3m[i] += v
            top3m[j] += v
            top3m[k] += v

    rate = cfg["model"]["trio_return_rate"]
    cands = {"honmei": [], "chuana": [], "oana": []}
    est_count = 0
    for (i, j, k), prob in tp.items():
        combo = tuple(sorted((horses[i]["umaban"], horses[j]["umaban"], horses[k]["umaban"])))
        o = real.get(combo)
        est = False
        if o is None:
            q = tpm[(i, j, k)] if tpm else prob
            o = max(1.0, round(rate / q, 1)) if q > 0 else 9999.0
            est = True
            est_count += 1
        ev = prob * o
        cands[band_of(o, cfg)].append({"c": list(combo), "p": round(prob, 5), "o": o, "ev": round(ev, 3), "est": est})

    lim = cfg["picks"]["candidates_per_band"]
    minp = cfg["picks"]["min_prob"]
    cands["honmei"].sort(key=lambda x: -x["p"])
    cands["chuana"] = [c for c in cands["chuana"] if c["p"] >= minp["chuana"]]
    cands["chuana"].sort(key=lambda x: -(x["ev"] ** 0.6 * x["p"] ** 0.4))
    cands["oana"] = [c for c in cands["oana"] if c["p"] >= minp["oana"]]
    cands["oana"].sort(key=lambda x: -x["ev"])
    for b in cands:
        cands[b] = cands[b][:lim]

    # 印
    order = sorted(range(n), key=lambda i: -p[i])
    marks = {}
    for sym, i in zip("◎○▲", order[:3]):
        marks[i] = sym
    for i in sorted(order[3:], key=lambda i: -top3[i])[:2]:
        marks[i] = "△"
    if use_market:
        ana = [i for i in range(n) if i not in marks and (wo.get(horses[i]["umaban"], (0, 0))[1] or 0) >= 6]
        ana.sort(key=lambda i: -(top3[i] - top3m[i]))
        if ana and top3[ana[0]] > top3m[ana[0]]:
            marks[ana[0]] = "☆"

    rows = []
    for i, h in enumerate(horses):
        f = h.get("feat") or {}
        o = wo.get(h["umaban"])
        rows.append({
            "u": h["umaban"], "w": h.get("waku"), "name": h.get("name"), "jockey": h.get("jockey"),
            "sexage": h.get("sexage"), "kinryo": h.get("kinryo"),
            "odds": o[0] if o else None, "ninki": o[1] if o else None,
            "p": round(p[i], 4), "top3": round(top3[i], 4), "top3m": round(top3m[i], 4) if use_market else None,
            "ability": round(50 + 10 * z[i], 1), "pad": round(pad[i][0], 3), "notes": pad[i][1],
            "weight": h.get("weight"), "wdiff": h.get("weight_diff"),
            "last5": f.get("last5"), "g_top3": f.get("g_top3"), "days_off": f.get("days_off"),
            "mark": marks.get(i),
        })
    rows.sort(key=lambda r: r["u"])
    scratched = [h.get("umaban") for h in race["horses"] if h.get("scratched")]
    return {
        "ok": True, "generated": now.isoformat(timespec="minutes"),
        "market": use_market, "win_status": win.get("status"),
        "trio_real": len(real) > 0, "trio_est_count": est_count,
        "weights_in": sum(1 for h in horses if h.get("weight")) >= n * 0.8,
        "paddock_on": mins is not None and mins <= 75,
        "horses": rows, "cands": cands, "scratched": [u for u in scratched if u],
    }


def default_picks(pred: dict, cfg: dict) -> list[dict]:
    """記録用：初期設定の配分（例 本命4・中穴3・大穴3）で選んだ10点。"""
    out = []
    for b, k in cfg["picks"]["default_alloc"].items():
        for c in (pred.get("cands") or {}).get(b, [])[:k]:
            out.append({**c, "b": b})
    return out


def settle(picks: list[dict], res: dict, unit: int) -> dict:
    """答え合わせ：当たった買い目と払戻。"""
    win = {tuple(t["combo"]): t["payout"] for t in res.get("trio", [])}
    ret, hits = 0, []
    for c in picks:
        pay = win.get(tuple(sorted(c["c"])))
        if pay:
            amt = c.get("amt", unit)
            ret += pay * amt // 100
            hits.append({**c, "payout": pay})
    return {"stake": sum(c.get("amt", unit) for c in picks), "return": ret, "hits": hits}
