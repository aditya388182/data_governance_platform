#!/usr/bin/env bash
# scripts/verify_stack.sh — verifies the dev stack from infra/docker-compose.yml.
# Exit 0 only if every component answers correctly.
# shellcheck disable=SC2015  # pass/ok/warn always return 0, so A && B || C is a safe if-else here
set -uo pipefail
FAILS=0
ok()  { printf '  [OK]   %s\n' "$1"; }
bad() { printf '  [FAIL] %s\n' "$1"; FAILS=$((FAILS+1)); }

echo "== Kafka"
if docker exec gov-kafka kafka-topics --bootstrap-server localhost:9092 --list >/dev/null 2>&1; then ok "broker answers inside the network (kafka:9092)"; else bad "broker not answering (docker logs gov-kafka)"; fi
if (exec 3<>/dev/tcp/127.0.0.1/29092) 2>/dev/null; then ok "host listener localhost:29092 open"; else bad "host listener localhost:29092 closed"; fi

echo "== Schema Registry (runtime, :8081)"
body=$(curl -sS --max-time 5 http://localhost:8081/subjects 2>/dev/null || true)
case "$body" in \[*) ok "GET /subjects -> $body" ;; *) bad "GET /subjects -> '${body:-no answer}' (docker logs gov-schema-registry)" ;; esac

echo "== MinIO"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:9000/minio/health/live || true)
[ "$code" = "200" ] && ok "health/live 200" || bad "health/live -> $code"
init_rc=$(docker inspect gov-minio-init --format '{{.State.ExitCode}}' 2>/dev/null || echo "?")
[ "$init_rc" = "0" ] && ok "minio-init exited 0" || bad "minio-init exit code $init_rc (docker logs gov-minio-init)"
MC_IMG="${MINIO_MC_IMAGE:-cgr.dev/chainguard/minio-client:latest}"
buckets=$(docker run --rm --network governance-dev_default \
  -e MC_HOST_chk=http://minioadmin:minioadmin123@minio:9000 \
  "$MC_IMG" --config-dir /tmp/.mc ls chk 2>/dev/null || true)
for b in governance-lake backups; do
  printf '%s' "$buckets" | grep -q "$b/" && ok "bucket $b exists" || bad "bucket $b missing (docker logs gov-minio-init)"
done

echo "== LocalStack (:4566)"
h=$(curl -s --max-time 5 http://localhost:4566/_localstack/health 2>/dev/null || true)
for svc in s3 dynamodb; do
  st=$(printf '%s' "$h" | jq -r ".services.$svc // \"missing\"" 2>/dev/null || echo "no-answer")
  case "$st" in available|running) ok "$svc: $st" ;; *) bad "$svc: $st (docker logs gov-localstack — see plan troubleshooting T6)" ;; esac
done

echo ""
[ "$FAILS" -eq 0 ] && { echo "STACK: ALL GREEN"; exit 0; } || { echo "STACK: $FAILS problem(s)"; exit 1; }
