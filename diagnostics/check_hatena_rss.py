import requests
import xml.etree.ElementTree as ET

urls = [
    "https://b.hatena.ne.jp/hotentry/entertainment.rss",
    "https://b.hatena.ne.jp/hotentry/social.rss",
]

results = []
for url in urls:
    try:
        r = requests.get(url, timeout=20)
        results.append(f"[{url}] status={r.status_code}, content-type={r.headers.get('content-type')}, len={len(r.content)}")
        results.append(f"  body先頭300字: {r.text[:300]}")
        try:
            root = ET.fromstring(r.content)
            items = root.findall(".//item")
            results.append(f"  パースOK, item数={len(items)}")
            if items:
                results.append(f"  1件目タイトル: {items[0].findtext('title')}")
        except Exception as e:
            results.append(f"  パースエラー: {e}")
    except Exception as e:
        results.append(f"[{url}] リクエストエラー: {e}")

output = "\n".join(results)
print(output)
with open("diagnostics/hatena_check_result.txt", "w", encoding="utf-8") as f:
    f.write(output)
