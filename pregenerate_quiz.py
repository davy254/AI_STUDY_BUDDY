"""
Pre-generate the quiz bank so quizzes load instantly (and cost no live API calls).
Calls your deployed generate-quiz edge function once per unit/topic.

    python pregenerate_quiz.py            # 3 questions per topic
    python pregenerate_quiz.py --n 4 --limit 10   # quick test on 10 topics

Safe to re-run: topics that already have questions are skipped.
Free LLM tiers are rate limited, so it pauses between calls.
"""
import argparse
import os
import re
import time

import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
URL = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
sb = create_client(URL, KEY)

ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=3, help="questions per topic")
ap.add_argument("--limit", type=int, default=0, help="only do the first N topics (0 = all)")
ap.add_argument("--delay", type=float, default=4.0, help="seconds between calls")
args = ap.parse_args()

# topics = distinct (domain, unit) from the notes
rows = sb.table("chunks").select("domain,unit").execute().data
seen, topics = set(), []
for r in rows:
    unit = re.sub(r"^\d+\s+", "", (r["unit"] or "").strip())   # "3 describe cloud" -> "describe cloud"
    if not unit or any(w in unit.lower() for w in ("introduction", "summary")):
        continue
    key = (r["domain"], unit)
    if key not in seen:
        seen.add(key)
        topics.append(key)

done = {r["topic"] for r in sb.table("quiz_questions").select("topic").execute().data}
todo = [(d, t) for d, t in topics if t not in done]
if args.limit:
    todo = todo[: args.limit]
print(f"{len(topics)} topics, {len(done)} already done, generating {len(todo)}")

ok = fail = 0
for i, (domain, topic) in enumerate(todo, 1):
    for attempt in (1, 2):
        try:
            r = requests.post(
                f"{URL}/functions/v1/generate-quiz",
                headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
                json={"topic": topic, "domain": domain, "n": args.n},
                timeout=120,
            )
            if r.status_code == 200:
                ok += 1
                print(f"[{i}/{len(todo)}] {topic}: {len(r.json()['questions'])} saved")
                break
            print(f"[{i}/{len(todo)}] {topic}: HTTP {r.status_code} {r.text[:150]}")
        except Exception as e:
            print(f"[{i}/{len(todo)}] {topic}: {e}")
        if attempt == 1:
            time.sleep(20)   # likely rate limit: wait and retry once
        else:
            fail += 1
    time.sleep(args.delay)

print(f"\nDone. {ok} topics ok, {fail} failed. Re-run to retry failures.")
print("Check:  select domain, count(*) from quiz_questions group by domain;")
