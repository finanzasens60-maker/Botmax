"""Une dos historiales (data/published.json) sin perder entradas: lo nuevo de este job + lo que ya estaba en GitHub."""
import json
import sys

mio, destino = sys.argv[1], sys.argv[2]
a = json.load(open(mio)).get("items", [])
b = json.load(open(destino)).get("items", [])
vistos, items = set(), []
for it in b + a:
    k = json.dumps(it, sort_keys=True, ensure_ascii=False)
    if k not in vistos:
        vistos.add(k)
        items.append(it)
json.dump({"items": items[-300:]}, open(destino, "w"), ensure_ascii=False, indent=2)
