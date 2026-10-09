#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ -e companion/.env.local ]; then echo 'Refusing to overwrite companion/.env.local. Use a clean demo checkout.' >&2; exit 1; fi
if curl --silent --max-time 1 http://127.0.0.1:8069/ >/dev/null || curl --silent --max-time 1 http://localhost:3000/ >/dev/null; then echo 'Demo ports 3000 and 8069 must be free. Existing services were not changed.' >&2; exit 1; fi
RUN="${GITHUB_RUN_ID:-local-$(date +%s)}"
NET="dojang-rehearsal-$RUN"
DB="$NET-db"
ODOO="$NET-odoo"
PRIVATE="$(mktemp -d)"
NEXT_PID=""
CREATED_ENV=0
cleanup() {
  if [ -n "$NEXT_PID" ]; then kill "$NEXT_PID" 2>/dev/null || true; fi
  docker rm -f "$ODOO" "$DB" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  rm -rf "$PRIVATE"
  if [ "$CREATED_ENV" = 1 ]; then rm -f "$ROOT/companion/.env.local"; fi
}
trap cleanup EXIT
mkdir -p rehearsal-evidence
export DOJANG_EVIDENCE_DIR="$ROOT/rehearsal-evidence"
PASSWORD="$(openssl rand -hex 24)"
echo "::add-mask::$PASSWORD"
docker build -t dojang-rehearsal . > rehearsal-evidence/docker-build.log 2>&1
docker pull postgres:16 > rehearsal-evidence/postgres-image.log 2>&1
docker network create --internal "$NET" >/dev/null
docker run -d --name "$DB" --network "$NET" -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD="$PASSWORD" -e POSTGRES_DB=postgres postgres:16 >/dev/null
for attempt in $(seq 1 60); do if docker exec "$DB" pg_isready -U odoo >/dev/null 2>&1; then break; fi; sleep 2; done
docker exec "$DB" pg_isready -U odoo
COMMON=(--db_host="$DB" --db_user=odoo --db_password="$PASSWORD" -d dojang_demo_rehearsal --addons-path=/opt/odoo/odoo/addons,/opt/odoo/addons,/mnt/extra-addons --max-cron-threads=0 --data-dir=/private/data)
MOUNTS=(--network "$NET" --user "$(id -u):$(id -g)" -v "$ROOT/addons:/mnt/extra-addons:ro" -v "$PRIVATE:/private")
docker run --rm "${MOUNTS[@]}" dojang-rehearsal "${COMMON[@]}" -i dojo_kiosk,dojo_credits --without-demo=all --test-enable --test-tags=/dojo_kiosk:TestKioskSessionFirstV2 --stop-after-init > rehearsal-evidence/install.log 2>&1
python - <<'PY'
import pathlib,re
log=pathlib.Path('rehearsal-evidence/install.log').read_text(errors='replace')
totals=re.findall(r'(\d+) failed, (\d+) error\(s\) of (\d+) tests',log)
if not totals or any(int(f) or int(e) for f,e,n in totals) or max(int(n) for f,e,n in totals)<19:
    raise SystemExit('Native regression gate failed or tests did not run')
PY
docker run --rm -i "${MOUNTS[@]}" -e DOJANG_ENV_OUTPUT=/private/frontend.env -e DOJANG_FIXTURE_OUTPUT=/private/fixture.json dojang-rehearsal shell "${COMMON[@]}" --no-http < tools/seed_demo.py > rehearsal-evidence/seed.log 2>&1
cp "$PRIVATE/frontend.env" companion/.env.local
CREATED_ENV=1
set -a
source "$PRIVATE/frontend.env"
set +a
while IFS='=' read -r key value; do case "$key" in *KEY*|*TOKEN*|*SECRET*) echo "::add-mask::$value";; esac; done < "$PRIVATE/frontend.env"
export DOJANG_FIXTURE_OUTPUT="$PRIVATE/fixture.json"
docker run -d --name "$ODOO" "${MOUNTS[@]}" -p 127.0.0.1:8069:8069 dojang-rehearsal "${COMMON[@]}" --http-interface=0.0.0.0 --no-database-list --db-filter='^dojang_demo_rehearsal$' >/dev/null
for attempt in $(seq 1 90); do if curl --silent --fail http://127.0.0.1:8069/web/login >/dev/null; then break; fi; sleep 2; done
curl --silent --fail http://127.0.0.1:8069/web/login >/dev/null
(cd companion && npm run build) > rehearsal-evidence/connected-build.log 2>&1
(cd companion && npm run start -- --hostname 127.0.0.1) > rehearsal-evidence/next-server.log 2>&1 &
NEXT_PID=$!
for attempt in $(seq 1 90); do if curl --silent --fail http://localhost:3000/integration/pair >/dev/null; then break; fi; sleep 1; done
curl --silent --fail http://localhost:3000/integration/pair >/dev/null
if [ "${DOJANG_INTERACTIVE_DEMO:-0}" = 1 ]; then
  printf '\nDemo is running at http://localhost:3000/integration/pair\n'
  printf 'Operator-only pairing keys are in %s. Do not share this file or screen-share its contents.\n' "$PRIVATE/frontend.env"
  printf 'Open the pairing screen in separate browser profiles for kiosk and staff. Press Ctrl-C here to remove only this disposable demo.\n'
  wait "$NEXT_PID"
  exit 0
fi
(cd companion && node tests/rehearsal/browser.cjs) 2>&1 | tee rehearsal-evidence/browser.log
attendance=$(docker exec "$DB" psql -U odoo -d dojang_demo_rehearsal -Atc 'SELECT count(*) FROM dojo_attendance_log;')
reviews=$(docker exec "$DB" psql -U odoo -d dojang_demo_rehearsal -Atc 'SELECT count(*) FROM dojo_companion_review_receipt;')
test "$attendance" = 2
test "$reviews" = 1
printf 'attendance_rows=%s\nreview_receipts=%s\n' "$attendance" "$reviews" > rehearsal-evidence/database-verification.txt
docker logs "$ODOO" > rehearsal-evidence/odoo-http.log 2>&1
printf 'PASS: browser, server gateway and Odoo database agree. Synthetic data only.\n' > rehearsal-evidence/rehearsal-result.txt
