#!/usr/bin/env bash
# End-to-end acceptance script (curl-level).
#
# Prerequisites:
#   - SSH tunnel to the remote PostgreSQL:  ssh -N -L 5432:127.0.0.1:5432 root@124.221.180.74
#   - Server running:  uv run uvicorn main:app --port 8000
#   - .env has LLM_API_KEY and EMBEDDING_* filled
#
# Usage: bash test/e2e.sh
set -euo pipefail

BASE="http://127.0.0.1:8000/api/v1"
PASS=0; FAIL=0

check() { # name, got, expected
  local name="$1" got="$2" want="$3"
  if [[ "$got" == *"$want"* ]]; then
    echo "✓ $name"; PASS=$((PASS+1))
  else
    echo "✗ $name — expected '$want', got: $(echo "$got" | head -c 300)"; FAIL=$((FAIL+1))
  fi
}

echo "== 1) healthz =="
H=$(curl -s --max-time 15 "$BASE/healthz")
check "healthz ok" "$H" '"status":"ok"'
check "pool healthy" "$H" '"state":"HEALTHY"'

echo "== 2) register / login =="
U="e2e_$(date +%s)"
curl -s -X POST "$BASE/auth/register" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$U\",\"password\":\"secret123\"}" > /dev/null
TOKEN=$(curl -s -X POST "$BASE/auth/login" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$U\",\"password\":\"secret123\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
AUTH="Authorization: Bearer $TOKEN"
check "login returns token" "$TOKEN" "eyJ"

echo "== 3) conversations =="
CID=$(curl -s -X POST "$BASE/conversations" -H "$AUTH" -H 'Content-Type: application/json' -d '{}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
check "create conversation" "$CID" "-"

echo "== 4) streaming chat (sandbox + SSE) =="
SSE=$(curl -sN --max-time 120 -X POST "$BASE/conversations/$CID/stream" \
  -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"content":"用 python 计算 1+1 并运行验证，只回复结果"}')
echo "$SSE" | head -20
check "SSE message event" "$SSE" "event: message"
check "SSE done event" "$SSE" "event: done"
check "sandbox execute used" "$SSE" '"name": "execute"'

echo "== 5) mirror + checkpointer rows =="
uv run python - <<PY
from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://admin:123456@127.0.0.1:5432/default_db")
with eng.connect() as c:
    msgs = c.execute(text("SELECT role FROM messages WHERE conversation_id=:c"), {"c": "$CID"}).scalars().all()
    cps = c.execute(text("SELECT count(*) FROM checkpoints WHERE thread_id LIKE :t"), {"t": "%:$CID"}).scalar()
    print(f"messages: {msgs}")
    print(f"checkpoint rows: {cps}")
PY

echo "== 6) pool replenished =="
sleep 3
P=$(curl -s "$BASE/pool" -H "$AUTH")
check "pool in_flight 0" "$P" '"in_flight":0'
check "pool idle back" "$P" '"idle_count":3'

echo "== 7) per-user concurrency limit (second request → 429) =="
( curl -sN --max-time 60 -X POST "$BASE/conversations/$CID/stream" \
    -H "$AUTH" -H 'Content-Type: application/json' \
    -d '{"content":"数到 20，每秒报一个数"}' > /tmp/e2e_stream_a.txt ) &
sleep 1
CODE=$(curl -s -o /tmp/e2e_stream_b.txt -w '%{http_code}' -X POST "$BASE/conversations/$CID/stream" \
  -H "$AUTH" -H 'Content-Type: application/json' -d '{"content":"hi"}')
wait
check "second concurrent request 429" "$CODE" "429"

echo "== 8) document upload + RAG =="
echo "产品说明：本系统支持多会话与知识库问答。用户名为 ${U}。" > /tmp/e2e_note.txt
DOC=$(curl -s -X POST "$BASE/documents" -H "$AUTH" -F "file=@/tmp/e2e_note.txt" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
for i in $(seq 1 20); do
  ST=$(curl -s "$BASE/documents/$DOC" -H "$AUTH" | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')
  [ "$ST" = "ready" ] && break
  [ "$ST" = "failed" ] && echo "document indexing failed" && exit 1
  sleep 2
done
check "document ready" "$ST" "ready"

RAG=$(curl -sN --max-time 120 -X POST "$BASE/conversations/$CID/stream" \
  -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"content":"根据知识库，本系统支持什么？"}' | grep -o '"name": "search_knowledge_base"' | head -1)
check "RAG tool used" "$RAG" "search_knowledge_base"

echo "== 9) RustFS objects =="
uv run python - <<PY
import boto3
from botocore.client import Config
s3 = boto3.client("s3", endpoint_url="http://127.0.0.1:9000",
  aws_access_key_id="123456", aws_secret_access_key="123456", region_name="us-east-1",
  config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))
keys = [o["Key"] for o in s3.list_objects_v2(Bucket="agent-files").get("Contents", [])]
docs = [k for k in keys if "/documents/" in k]
print(f"document objects: {docs}")
PY

echo "== 10) deletion cascades =="
curl -s -X DELETE "$BASE/documents/$DOC" -H "$AUTH" -o /dev/null
uv run python - <<PY
from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://admin:123456@127.0.0.1:5432/default_db")
with eng.connect() as c:
    chunks = c.execute(text("SELECT count(*) FROM chunks WHERE document_id=:d"), {"d": "$DOC"}).scalar()
    print(f"chunks after delete: {chunks}")
PY
curl -s -X DELETE "$BASE/conversations/$CID" -H "$AUTH" -o /dev/null
uv run python - <<PY
from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://admin:123456@127.0.0.1:5432/default_db")
with eng.connect() as c:
    cps = c.execute(text("SELECT count(*) FROM checkpoints WHERE thread_id LIKE :t"), {"t": "%:$CID"}).scalar()
    msgs = c.execute(text("SELECT count(*) FROM messages WHERE conversation_id=:c"), {"c": "$CID"}).scalar()
    print(f"checkpoint rows after conv delete: {cps}, mirror rows: {msgs}")
PY

echo
echo "== PASS: $PASS  FAIL: $FAIL =="
[ "$FAIL" = 0 ]
