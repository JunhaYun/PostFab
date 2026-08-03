import json, os
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
c = json.load(open(os.path.join(BASE, "..", "data", "corpus_raw.json"), encoding="utf-8"))
lines = [f"{d['id']}\t{d['source']}\t{d['title']}" for d in c["documents"]]
open(os.path.join(BASE, "scripts", "_corpus_titles.txt"), "w", encoding="utf-8").write("\n".join(lines))
print("wrote", len(lines))
