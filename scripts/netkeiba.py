"""netkeiba からの取得と読み取り。

取りに行くのは次の5種類だけです（1回ごとに1秒以上あけ、必要なときだけ取りに行きます）。
  1. その日のレース一覧          race.netkeiba.com/top/race_list_sub.html
  2. 出馬表（馬体重・取消もここ）  race.netkeiba.com/race/shutuba.html
  3. オッズ（単勝・3連複）        race.netkeiba.com/api/api_get_jra_odds.html
  4. 馬の過去成績                db.netkeiba.com/horse/result/<馬ID>/
  5. レース結果と払戻            race.netkeiba.com/race/result.html

ページの作りが変わっても止まりにくいように、細かい飾り（クラス名）よりも
「見出しの文字」や「リンクの形」で読み取るようにしています。
読み取れなかったときは data/debug/ に元のページを残します（直すときの手がかり）。
"""
from __future__ import annotations

import base64
import json
import re
import time
import zlib

import requests
from bs4 import BeautifulSoup

from common import DEBUG, VENUE_CODE, VENUES

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
_last = [0.0]
REQUEST_COUNT = [0]
WAIT = 1.2  # 1回ごとの間隔（秒）

GRADE_ICON = {"1": "G1", "2": "G2", "3": "G3"}   # Icon_GradeType1〜3（10〜12は障害重賞）


# ------------------------------------------------------------------ 通信
def _decode(r: requests.Response) -> str:
    raw = r.content
    ctype = r.headers.get("content-type", "").lower()
    m = re.search(r"charset=([\w-]+)", ctype)
    enc = m.group(1) if m else None
    if not enc:
        m = re.search(rb"charset=[\"']?([\w-]+)", raw[:3000])
        enc = m.group(1).decode() if m else None
    for e in [enc, "utf-8", "euc_jp", "cp932"]:
        if not e:
            continue
        try:
            return raw.decode(e)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def get(url: str, referer: str | None = None, retries: int = 3) -> str | None:
    last = None
    for i in range(retries):
        wait = WAIT - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            h = {"Referer": referer} if referer else {}
            r = _session.get(url, headers=h, timeout=30)
            _last[0] = time.time()
            REQUEST_COUNT[0] += 1
            if r.status_code == 404:
                return None
            if r.status_code >= 400:
                last = f"HTTP {r.status_code}"
                time.sleep(3 * (i + 1))
                continue
            return _decode(r)
        except requests.RequestException as e:
            last = e
            time.sleep(3 * (i + 1))
    print(f"  ! 取得できませんでした: {url} ({last})")
    return None


def save_debug(name: str, text: str | None) -> None:
    if not text:
        return
    DEBUG.mkdir(parents=True, exist_ok=True)
    (DEBUG / name).write_text(text[:400000], encoding="utf-8")


def _txt(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el is not None else ""


def _nospace(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


# ------------------------------------------------------------------ 1. レース一覧
def race_list(ymd: str) -> list[dict]:
    """その日のJRAのレース一覧。重賞かどうか（G1〜G3）も付けて返す。"""
    url = f"https://race.netkeiba.com/top/race_list_sub.html?kaisai_date={ymd}"
    html = get(url, referer="https://race.netkeiba.com/top/race_list.html")
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for a in soup.find_all("a", href=re.compile(r"race_id=\d{12}")):
        rid = re.search(r"race_id=(\d{12})", a["href"]).group(1)
        if rid in seen or rid[4:6] not in VENUES:
            continue
        # その馬券1レース分のまとまり（li）を探す
        box = a.find_parent("li") or a
        text = _txt(box)
        if not re.search(r"\d+R", text) and not box.select_one("[class*=Race_Num]"):
            continue
        seen.add(rid)
        grade = None
        for el in box.select("[class*=Icon_GradeType]"):
            for c in el.get("class", []):
                m = re.fullmatch(r"Icon_GradeType(\d+)", c)
                if m and m.group(1) in GRADE_ICON:
                    grade = GRADE_ICON[m.group(1)]
        name_el = box.select_one(".ItemTitle") or box.select_one("[class*=ItemTitle]")
        name = _txt(name_el) if name_el else ""
        if not grade:
            grade = grade_from_name(name or text)
        tm = re.search(r"(\d{1,2}:\d{2})", text)
        cs = re.search(r"(芝|ダ|障)\D{0,3}(\d{3,4})m", text)
        hd = re.search(r"(\d+)頭", text)
        out.append({
            "race_id": rid, "date": ymd, "venue": VENUES[rid[4:6]], "rno": int(rid[10:12]),
            "name": name or text[:30], "grade": grade, "time": tm.group(1) if tm else None,
            "surface": cs.group(1) if cs else None, "distance": int(cs.group(2)) if cs else None,
            "heads": int(hd.group(1)) if hd else None,
        })
    if not out and "race_id" in html:
        save_debug(f"race_list_{ymd}.html", html)
    return out


def grade_from_name(name: str) -> str | None:
    s = _nospace(name).upper().replace("Ｇ", "G").replace("Ⅲ", "III").replace("Ⅱ", "II").replace("Ⅰ", "I")
    if re.search(r"J\.?G", s):
        return None  # 障害重賞は対象外
    for g, pats in (("G1", ("G1)", "GI)")), ("G2", ("G2)", "GII)")), ("G3", ("G3)", "GIII)"))):
        if any(p in s for p in pats):
            return g
    return None


# ------------------------------------------------------------------ 2. 出馬表
def shutuba(race_id: str) -> dict | None:
    url = f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
    html = get(url, referer="https://race.netkeiba.com/top/race_list.html")
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")
    info: dict = {"race_id": race_id}
    rn = soup.select_one(".RaceName")
    if rn is not None:
        info["name"] = _txt(rn)
        for el in rn.select("[class*=Icon_GradeType]"):
            for c in el.get("class", []):
                m = re.fullmatch(r"Icon_GradeType(\d+)", c)
                if m and m.group(1) in GRADE_ICON:
                    info["grade"] = GRADE_ICON[m.group(1)]
    d1 = _txt(soup.select_one(".RaceData01"))
    m = re.search(r"(\d{1,2}:\d{2})発走", d1)
    info["time"] = m.group(1) if m else None
    m = re.search(r"(芝|ダ|障)(\d{3,4})m", d1)
    if m:
        info["surface"], info["distance"] = m.group(1), int(m.group(2))
    m = re.search(r"\((右|左|直線)", d1)
    info["turn"] = m.group(1) if m else None
    m = re.search(r"天候:\s*(\S+)", d1)
    info["weather"] = m.group(1) if m else None
    m = re.search(r"馬場:\s*(\S+)", d1)
    info["going"] = m.group(1) if m else None
    info["data2"] = _txt(soup.select_one(".RaceData02"))

    horses = []
    rows = soup.select("tr.HorseList") or [
        tr for tr in soup.find_all("tr") if tr.find("a", href=re.compile(r"/horse/\d{10}"))]
    for tr in rows:
        a = tr.find("a", href=re.compile(r"/horse/\d{10}"))
        if a is None:
            continue
        h = {"horse_id": re.search(r"/horse/(\d{10})", a["href"]).group(1),
             "name": a.get("title") or _txt(a)}
        tds = tr.find_all("td")
        for td in tds:
            cls = " ".join(td.get("class", []))
            t = _txt(td)
            if re.search(r"\bWaku\d", cls) and t.isdigit():
                h["waku"] = int(t)
            elif re.search(r"\bUmaban\d", cls) and t.isdigit():
                h["umaban"] = int(t)
            elif "Barei" in cls:
                h["sexage"] = t
            elif "Jockey" in cls:
                h["jockey"] = t
                ja = td.find("a", href=True)
                if ja:
                    mm = re.search(r"/jockey/(?:result/recent/|profile/)?(\w+)", ja["href"])
                    h["jockey_id"] = mm.group(1) if mm else None
            elif "Trainer" in cls:
                h["trainer"] = t
            elif "Weight" in cls:
                h.update(parse_weight(t))
        # 斤量：性齢の次の数字
        for i, td in enumerate(tds):
            if "Barei" in " ".join(td.get("class", [])) and i + 1 < len(tds):
                mm = re.fullmatch(r"(\d{2}(?:\.\d)?)", _txt(tds[i + 1]))
                if mm:
                    h["kinryo"] = float(mm.group(1))
        o = tr.select_one("[id^=odds-]")
        if o is not None:
            try:
                h["odds"] = float(_txt(o))
            except ValueError:
                pass
        rowtxt = _txt(tr)
        cls = " ".join(tr.get("class", []))
        h["scratched"] = bool("Cancel" in cls or re.search(r"取消|除外", rowtxt))
        # 枠・馬番が決まる前（出馬表確定前）は空のまま
        if "umaban" not in h:
            nums = [int(_txt(td)) for td in tds[:2] if _txt(td).isdigit()]
            if len(nums) == 2:
                h["waku"], h["umaban"] = nums
        horses.append(h)
    info["horses"] = horses
    if not horses:
        save_debug(f"shutuba_{race_id}.html", html)
    return info


def parse_weight(t: str) -> dict:
    m = re.search(r"(\d{3})\s*\(?\s*([+\-－]?\d+|前計不|計不)?", t or "")
    if not m:
        return {}
    out = {"weight": int(m.group(1))}
    d = m.group(2)
    if d and re.fullmatch(r"[+\-－]?\d+", d):
        out["weight_diff"] = int(d.replace("－", "-").replace("+", ""))
    return out


# ------------------------------------------------------------------ 3. オッズ
def _odds_api(race_id: str, typ, action: str) -> dict | None:
    url = ("https://race.netkeiba.com/api/api_get_jra_odds.html?pid=api_get_jra_odds&input=UTF-8"
           f"&output=json&race_id={race_id}&type={typ}&action={action}&sort=odds&compress=0")
    txt = get(url, referer=f"https://race.netkeiba.com/odds/index.html?race_id={race_id}")
    if not txt:
        return None
    txt = txt.strip()
    if txt.startswith("(") or re.match(r"^\w+\(", txt):  # jsonp で返ってきたとき
        txt = txt[txt.index("(") + 1: txt.rindex(")")]
    try:
        js = json.loads(txt)
    except json.JSONDecodeError:
        save_debug(f"odds_{race_id}_{typ}.txt", txt)
        return None
    data = js.get("data")
    if isinstance(data, str) and data:  # 圧縮されていたとき
        raw = base64.b64decode(data)
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS, zlib.MAX_WBITS | 32):
            try:
                data = json.loads(zlib.decompress(raw, wbits).decode("utf-8"))
                break
            except Exception:
                continue
        js["data"] = data if isinstance(data, dict) else {}
    return js


def win_odds(race_id: str) -> dict:
    """単勝オッズ。{'status':..., 'odds': {馬番: (倍率, 人気)}, 'time': 公式の時刻}"""
    js = _odds_api(race_id, 1, "update") or {}
    data = js.get("data") or {}
    tbl = (data.get("odds") or {}).get("1") or {}
    odds = {}
    for k, v in tbl.items():
        try:
            o = float(v[0])
            if o > 0:
                odds[int(k)] = (o, int(v[2]) if len(v) > 2 and str(v[2]).isdigit() else None)
        except (ValueError, TypeError, IndexError):
            continue
    return {"status": js.get("status"), "odds": odds,
            "time": data.get("official_datetime") or data.get("update_datetime")}


def trio_odds(race_id: str) -> dict:
    """3連複オッズ。{'status':..., 'odds': {(a,b,c): 倍率}}。取れなければ odds は空。"""
    for typ, action in ((7, "update"), (7, "init"), ("b7", "update")):
        js = _odds_api(race_id, typ, action) or {}
        data = js.get("data") or {}
        allodds = data.get("odds") or {}
        tbl = allodds.get("7") or allodds.get("b7") or {}
        if not tbl:  # 種類の番号が違うときは、6けたの組み合わせが入っている表を探す
            for v in allodds.values():
                if isinstance(v, dict) and v and all(len(k) == 6 for k in list(v)[:5]):
                    tbl = v
                    break
        odds = {}
        for k, v in tbl.items():
            if len(k) != 6:
                continue
            try:
                o = float(v[0] if isinstance(v, list) else v)
            except (ValueError, TypeError):
                continue
            if o > 0:
                odds[tuple(sorted((int(k[0:2]), int(k[2:4]), int(k[4:6]))))] = o
        if odds:
            return {"status": js.get("status"), "odds": odds, "source": f"type={typ}&action={action}"}
    return {"status": None, "odds": {}, "source": None}


# ------------------------------------------------------------------ 4. 馬の過去成績
def horse_results(horse_id: str) -> list[dict] | None:
    url = f"https://db.netkeiba.com/horse/result/{horse_id}/"
    html = get(url, referer=f"https://db.netkeiba.com/horse/{horse_id}/")
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")
    table = None
    for t in soup.find_all("table"):
        heads = [_nospace(_txt(th)) for th in t.find_all("th")]
        if "着順" in heads and "距離" in heads:
            table = t
            break
    if table is None:
        if "着順" not in html:
            return []  # まだ走ったことがない馬
        save_debug(f"horse_{horse_id}.html", html)
        return None
    heads = [_nospace(_txt(th)) for th in table.find("tr").find_all(["th", "td"])]
    idx = {h: i for i, h in enumerate(heads)}

    def cell(tds, key):
        i = idx.get(key)
        return tds[i] if i is not None and i < len(tds) else None

    rows = []
    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) < 10:
            continue
        date = _txt(cell(tds, "日付"))
        if not re.match(r"\d{4}/\d{2}/\d{2}", date):
            continue
        kaisai = _txt(cell(tds, "開催"))
        rname_td = cell(tds, "レース名")
        rname = _txt(rname_td)
        rid = None
        if rname_td is not None and rname_td.find("a", href=True):
            mm = re.search(r"/race/(\d{12})", rname_td.find("a")["href"])
            rid = mm.group(1) if mm else None
        venue = None
        mm = re.search(r"\d*(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉)", kaisai)
        if mm:
            venue = mm.group(1)
        dist = _txt(cell(tds, "距離"))
        md = re.search(r"(芝|ダ|障)(\d{3,4})", dist)
        rank_t = _txt(cell(tds, "着順"))
        heads_t = _txt(cell(tds, "頭数"))
        margin_t = _txt(cell(tds, "着差"))
        agari_t = _txt(cell(tds, "上り"))
        r = {
            "date": date.replace("/", "-"), "venue": venue, "race_id": rid, "name": rname,
            "grade": grade_from_name(rname) or class_from_name(rname),
            "surface": md.group(1) if md else None, "distance": int(md.group(2)) if md else None,
            "going": _txt(cell(tds, "馬場")),
            "heads": int(heads_t) if heads_t.isdigit() else None,
            "rank": int(rank_t) if rank_t.isdigit() else None,
            "margin": _num(margin_t), "agari": _num(agari_t),
            "ninki": _int(_txt(cell(tds, "人気"))), "odds": _num(_txt(cell(tds, "オッズ"))),
            "jra": venue is not None,
        }
        r.update(parse_weight(_txt(cell(tds, "馬体重"))))
        rows.append(r)
    return rows


def class_from_name(name: str) -> str | None:
    s = _nospace(name)
    for k, v in (("3勝", "3勝"), ("1600万", "3勝"), ("2勝", "2勝"), ("1000万", "2勝"),
                 ("1勝", "1勝"), ("500万", "1勝"), ("未勝利", "未勝利"), ("新馬", "新馬")):
        if k in s:
            return v
    if re.search(r"\(L\)|リステッド", s):
        return "L"
    if re.search(r"\(OP\)|オープン", s):
        return "OP"
    return None


def _num(t: str):
    try:
        return float(t)
    except (TypeError, ValueError):
        return None


def _int(t: str):
    return int(t) if t and t.isdigit() else None


# ------------------------------------------------------------------ 5. 結果
def result(race_id: str) -> dict | None:
    """着順（上位3頭）と3連複の払戻。まだ確定していなければ None。"""
    url = f"https://race.netkeiba.com/race/result.html?race_id={race_id}"
    html = get(url, referer=f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}")
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")
    trio = []
    for tr in soup.find_all("tr"):
        th = tr.find("th")
        if th is None or _nospace(_txt(th)) not in ("3連複", "三連複"):
            continue
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        nums = [int(x) for x in re.findall(r"\d+", _txt(tds[0]))]
        pays = [int(x.replace(",", "")) for x in re.findall(r"([\d,]+)円", _txt(tds[1]))]
        for j, p in enumerate(pays):
            combo = nums[j * 3:(j + 1) * 3]
            if len(combo) == 3:
                trio.append({"combo": sorted(combo), "payout": p})
        break
    order = []
    for t in soup.find_all("table"):
        heads = [_nospace(_txt(th)) for th in t.find_all("th")]
        if "着順" in heads and "馬番" in heads:
            hi = heads.index("着順")
            ui = heads.index("馬番")
            for tr in t.find_all("tr")[1:]:
                tds = tr.find_all("td")
                if len(tds) > max(hi, ui):
                    rk, ub = _txt(tds[hi]), _txt(tds[ui])
                    if rk.isdigit() and ub.isdigit():
                        order.append((int(rk), int(ub)))
            break
    if not trio:
        if "払戻" in html and order:
            save_debug(f"result_{race_id}.html", html)
        return None
    order.sort()
    return {"trio": trio, "top": [u for _, u in order[:5]]}


# ------------------------------------------------------------------ 補助
def venue_code(name: str | None) -> str | None:
    return VENUE_CODE.get(name or "")
