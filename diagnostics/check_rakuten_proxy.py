import os
import requests

proxy_url = os.environ["RAKUTEN_PROXY_URL"]
proxy_secret = os.environ["RAKUTEN_PROXY_SECRET"]

results = []
for kw in ["コーヒー", "引越し手伝いのお礼問題", "引越し"]:
    r = requests.get(proxy_url, params={"secret": proxy_secret, "keyword": kw}, timeout=20)
    results.append(f"[{kw}] status={r.status_code} body={r.text[:500]}")

with open("diagnostics/rakuten_proxy_result.txt", "w", encoding="utf-8") as f:
    f.write("\n\n".join(results))
print("\n\n".join(results))
