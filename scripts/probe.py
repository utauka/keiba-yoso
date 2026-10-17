"""動作確認用：本物のページを読み取れるかを試し、結果を data/debug/probe.json に残す。"""
import json, sys, traceback
sys.path.insert(0, "scripts")
import netkeiba as nk
from common import DEBUG

out = {}
def run(name, fn):
    try:
        out[name] = fn()
    except Exception:
        out[name] = {"error": traceback.format_exc()[-1500:]}

days = sys.argv[1:] or ["20261017", "20261018"]
for d in days:
    run(f"list_{d}", lambda d=d: nk.race_list(d))
graded = [r for d in days for r in (out.get(f"list_{d}") or []) if isinstance(r, dict) and r.get("grade")]
out["graded"] = graded
rid = graded[-1]["race_id"] if graded else None
if rid:
    run("shutuba", lambda: nk.shutuba(rid))
    run("win", lambda: {"status": (w := nk.win_odds(rid))["status"], "n": len(w["odds"]), "sample": list(w["odds"].items())[:3]})
    run("trio", lambda: {"status": (t := nk.trio_odds(rid))["status"], "src": t["source"], "n": len(t["odds"]), "sample": [list(k)+[v] for k, v in list(t["odds"].items())[:3]]})
    hs = (out.get("shutuba") or {}).get("horses") or []
    if hs:
        run("horse", lambda: (lambda r: {"n": len(r or []), "rows": (r or [])[:3]})(nk.horse_results(hs[0]["horse_id"])))
    # 先週の重賞の結果ページ
    for raw in [nk.get(f"https://race.netkeiba.com/api/api_get_jra_odds.html?pid=api_get_jra_odds&input=UTF-8&output=json&race_id={rid}&type=7&action=update&sort=odds&compress=0")]:
        nk.save_debug("raw_trio.txt", raw)
    nk.save_debug("raw_shutuba.html", nk.get(f"https://race.netkeiba.com/race/shutuba.html?race_id={rid}"))
run("result_old", lambda: nk.result("202508040411"))
nk.save_debug("raw_result.html", nk.get("https://race.netkeiba.com/race/result.html?race_id=202508040411"))
nk.save_debug("raw_list.html", nk.get(f"https://race.netkeiba.com/top/race_list_sub.html?kaisai_date={days[-1]}"))
DEBUG.mkdir(parents=True, exist_ok=True)
(DEBUG / "probe.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, default=str)[:3000])
