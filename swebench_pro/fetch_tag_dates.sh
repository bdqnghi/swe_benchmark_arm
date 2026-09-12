#!/bin/bash
# Fetch push dates of every jefzda/sweap-images tag from the Docker Hub API (11 pages of 100).
: > tag_pages.jsonl
for page in $(seq 1 11); do
  url="https://hub.docker.com/v2/repositories/jefzda/sweap-images/tags?page=$page&page_size=100"
  for i in $(seq 1 20); do
    code=$(curl -s -o /tmp/claude-1000/tagpage.json -w '%{http_code}' -m 60 "$url")
    [ "$code" = 200 ] && python3 -c "import json; json.load(open('/tmp/claude-1000/tagpage.json'))" 2>/dev/null && break
    echo "page $page attempt $i status $code; sleeping"; sleep 30
  done
  cat /tmp/claude-1000/tagpage.json >> tag_pages.jsonl; echo >> tag_pages.jsonl
  echo "fetched page $page"
done
python3 - <<'PY'
import json
out = {}
for line in open("tag_pages.jsonl"):
    if line.strip():
        for t in json.loads(line)["results"]:
            out[t["name"]] = {"last_updated": t["last_updated"], "tag_last_pushed": t.get("tag_last_pushed")}
json.dump(out, open("tag_dates.json", "w"), indent=1)
print("tags:", len(out))
PY
