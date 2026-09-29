#!/usr/bin/env bash
set -uo pipefail

FAILS=0; WARNS=0
pass() { printf '  [PASS] %s\n' "$1"; }
warn() { printf '  [WARN] %s\n' "$1"; WARNS=$((WARNS+1)); }
fail() { printf '  [FAIL] %s\n         fix: %s\n' "$1" "$2"; FAILS=$((FAILS+1)); }
# ver_ge A B  -> true if version A >= version B (numeric dotted compare)
ver_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -t. -k1,1n -k2,2n -k3,3n | head -1)" = "$2" ]; }
OS="$(uname -s)"
if [ "$OS" = "Darwin" ]; then PKG="brew install"; else PKG="sudo apt-get install -y"; fi

echo "== Tools"
if command -v git >/dev/null; then
  v=$(git --version | awk '{print $3}'); ver_ge "$v" 2.30 && pass "git $v" || fail "git $v < 2.30" "$PKG git"
  if [ -n "$(git config --global user.name)" ] && [ -n "$(git config --global user.email)" ]; then pass "git identity: $(git config --global user.name) <$(git config --global user.email)>"
  else fail "git user.name/user.email not set" "git config --global user.name 'Your Name'; git config --global user.email you@example.com"; fi
else fail "git missing" "$PKG git"; fi

if command -v jq >/dev/null; then
  v=$(jq --version | sed 's/^jq-//'); ver_ge "$v" 1.6 && pass "jq $v (supports --rawfile)" || fail "jq $v < 1.6" "$PKG jq"
else fail "jq missing" "$PKG jq"; fi

command -v curl >/dev/null && pass "curl $(curl --version | head -1 | awk '{print $2}')" || fail "curl missing" "$PKG curl"
command -v perl >/dev/null && pass "perl present (used for portable in-place edits)" || fail "perl missing" "$PKG perl"

PY=""
for c in python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null; then
    pv=$("$c" -c 'import sys;print("%d.%d"%sys.version_info[:2])')
    case "$pv" in 3.10|3.11) PY="$c"; break ;; esac
  fi
done
if [ -n "$PY" ]; then pass "python for the venv: $PY ($("$PY" --version 2>&1))"
else fail "need Python 3.11 (or 3.10) — PySpark 3.5.1 / GE 0.18 are pinned for it" \
  "macOS: brew install python@3.11   Ubuntu 22.04: sudo apt-get install -y python3.10-venv   Ubuntu 24.04: sudo add-apt-repository -y ppa:deadsnakes/ppa && sudo apt-get install -y python3.11 python3.11-venv"; fi

echo "== GitHub CLI"
if command -v gh >/dev/null; then
  v=$(gh --version | head -1 | awk '{print $3}'); ver_ge "$v" 2.40 && pass "gh $v" || warn "gh $v is old — upgrade recommended ($PKG gh)"
  st=$(gh auth status 2>&1 || true)
  if printf '%s' "$st" | grep -qi "logged in"; then
    pass "gh authenticated ($(printf '%s' "$st" | grep -io 'account [^ ]*' | head -1))"
    scopes=$(printf '%s' "$st" | grep -i "token scopes" | head -1)
    printf '%s' "$scopes" | grep -q "'repo'" && pass "token scope: repo" || fail "token lacks 'repo' scope" "gh auth refresh -h github.com -s repo"
    printf '%s' "$scopes" | grep -q "'workflow'" && pass "token scope: workflow (needed to push .github/workflows)" \
      || fail "token lacks 'workflow' scope — pushing .github/workflows/* will be REJECTED" "gh auth refresh -h github.com -s workflow"
  else fail "gh not authenticated" "gh auth login -h github.com -p https -w   (then: gh auth refresh -h github.com -s workflow)"; fi
else fail "gh missing" "$PKG gh"; fi

echo "== Docker"
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  pass "docker daemon running ($(docker version --format '{{.Server.Version}}' 2>/dev/null))"
  cv=$(docker compose version --short 2>/dev/null | sed 's/^v//')
  if [ -n "$cv" ] && ver_ge "$cv" 2.20; then pass "docker compose v$cv"; else fail "docker compose v2.20+ required (found '${cv:-none}')" "update Docker Desktop / install docker-compose-plugin"; fi
  mem=$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0); gb=$(( mem / 1073741824 ))
  if [ "$gb" -ge 8 ]; then pass "docker memory ${gb} GiB"
  elif [ "$gb" -ge 6 ]; then warn "docker memory ${gb} GiB — OK for Day 1, raise to 8 GiB before Day 4 (Docker Desktop > Settings > Resources)"
  else fail "docker memory ${gb} GiB (< 6)" "Docker Desktop > Settings > Resources > Memory >= 8 GB"; fi
else fail "docker not running" "start Docker Desktop (macOS) or: sudo systemctl start docker (Ubuntu; add yourself to the docker group)"; fi

echo "== Ports (must be free)"
for p in 8081 18081 9000 9001 4566 29092; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then
    fail "port $p is in use" "stop whatever owns it (macOS: lsof -nP -iTCP:$p -sTCP:LISTEN; Ubuntu: sudo ss -ltnp | grep :$p) — often an old Project 1/2/3 compose stack: docker ps"
  else pass "port $p free"; fi
done

echo "== Disk"
free_gb=$(df -Pk . | awk 'NR==2 {print int($4/1048576)}')
[ "$free_gb" -ge 15 ] && pass "${free_gb} GiB free" || warn "${free_gb} GiB free — images + venv need ~10 GiB"

echo ""
if [ "$FAILS" -eq 0 ]; then echo "PREFLIGHT: GO  ($WARNS warning(s))"; exit 0
else echo "PREFLIGHT: NO-GO — $FAILS failure(s), $WARNS warning(s). Fix the FAIL lines above and re-run."; exit 1; fi
