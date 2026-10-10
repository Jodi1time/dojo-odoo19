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
RELAY_PID=""
CREATED_ENV=0
cleanup() {
  # Capture startup failures too; the previous runner discarded the only server evidence.
  docker logs "$ODOO" > rehearsal-evidence/odoo-http.log 2>&1 || true
  docker inspect --format 'status={{.State.Status}} exit={{.State.ExitCode}} ports={{json .NetworkSettings.Ports}}' "$ODOO" > rehearsal-evidence/odoo-container-state.txt 2>&1 || true
  if [ -n "$NEXT_PID" ]; then kill "$NEXT_PID" 2>/dev/null || true; fi
  if [ -n "$RELAY_PID" ]; then kill "$RELAY_PID" 2>/dev/null || true; fi
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
INSTALL=dojo_companion_hub
TAGS=/dojo_kiosk:TestKioskSessionFirstV2,/dojo_companion_hub:TestCompanionHub
if [ -n "${DOJANG_EB_GYM_ADDONS:-}" ]; then
  test -f "$DOJANG_EB_GYM_ADDONS/eb_gym_management/__manifest__.py"
  MOUNTS+=(-v "$DOJANG_EB_GYM_ADDONS:/mnt/private-addons:ro")
  COMMON+=(--addons-path=/opt/odoo/odoo/addons,/opt/odoo/addons,/mnt/extra-addons,/mnt/private-addons)
  INSTALL=dojo_eb_gym_bridge
  TAGS+=,/dojo_eb_gym_bridge:TestGymBridge
fi
docker run --rm "${MOUNTS[@]}" dojang-rehearsal "${COMMON[@]}" -i "$INSTALL" --without-demo=all --test-enable --test-tags="$TAGS" --stop-after-init > rehearsal-evidence/install.log 2>&1
python - <<'PY'
import ast,os,pathlib,re
log=pathlib.Path('rehearsal-evidence/install.log').read_text(errors='replace')
totals=re.findall(r'(\d+) failed, (\d+) error\(s\) of (\d+) tests',log)
expected=[]
sources=[('addons/dojo_kiosk/tests/test_kiosk_v2.py','TestKioskSessionFirstV2'),('addons/dojo_companion_hub/tests/test_hub.py','TestCompanionHub')]
if os.environ.get('DOJANG_EB_GYM_ADDONS'):
    sources.append(('addons/dojo_eb_gym_bridge/tests/test_bridge.py','TestGymBridge'))
for source,klass in sources:
    expected += [klass+'.'+n.name for n in ast.walk(ast.parse(pathlib.Path(source).read_text())) if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')]
if not totals or any(int(f) or int(e) for f,e,n in totals) or any(name not in log for name in expected) or max(int(n) for f,e,n in totals)<len(expected):
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
docker run -d --name "$ODOO" "${MOUNTS[@]}" --env-file "$PRIVATE/frontend.env" -p 127.0.0.1:8069:8069 dojang-rehearsal "${COMMON[@]}" --http-interface=0.0.0.0 --no-database-list --db-filter='^dojang_demo_rehearsal$' >/dev/null
# Distinguish a failed Odoo process from a missing host port mapping on an internal network.
for attempt in $(seq 1 60); do
  if [ "$(docker inspect --format '{{.State.Running}}' "$ODOO")" != true ]; then echo 'Odoo exited during startup; see odoo-http.log.' >&2; exit 1; fi
  if docker exec "$ODOO" curl --silent --fail --max-time 2 http://127.0.0.1:8069/web/login >/dev/null; then break; fi
  sleep 1
done
docker exec "$ODOO" curl --silent --fail --max-time 5 http://127.0.0.1:8069/web/login >/dev/null
if ! curl --silent --fail --max-time 3 http://127.0.0.1:8069/web/login >/dev/null; then
  # Linux hosts can reach an internal bridge's container IP directly. Keep Odoo's
  # external network isolation and expose only this test service on host loopback.
  test "$(uname -s)" = Linux
  ODOO_IP=$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$ODOO")
  curl --noproxy '*' --silent --fail --max-time 5 "http://$ODOO_IP:8069/web/login" >/dev/null
  node tools/rehearsal_loopback.cjs "$ODOO_IP" > rehearsal-evidence/loopback-relay.log 2>&1 &
  RELAY_PID=$!
fi
for attempt in $(seq 1 20); do if curl --silent --fail --max-time 2 http://127.0.0.1:8069/web/login >/dev/null; then break; fi; sleep 1; done
curl --silent --fail --max-time 5 http://127.0.0.1:8069/web/login >/dev/null
(cd companion && npm run build) > rehearsal-evidence/connected-build.log 2>&1
(cd companion && npm run start -- --hostname 127.0.0.1) > rehearsal-evidence/next-server.log 2>&1 &
NEXT_PID=$!
for attempt in $(seq 1 90); do if curl --silent --fail http://localhost:3000/integration/pair >/dev/null; then break; fi; sleep 1; done
curl --silent --fail http://localhost:3000/integration/pair >/dev/null
if [ "${DOJANG_INTERACTIVE_DEMO:-0}" = 1 ]; then
  python tools/seed_demo_hub_event.py
  printf '\nDemo is running at http://localhost:3000/integration/pair\n'
  printf 'Operator-only pairing keys are in %s. Do not share this file or screen-share its contents.\n' "$PRIVATE/frontend.env"
  printf 'The synthetic individual hub login/password are in %s. Sign in at http://localhost:3000/hub.\n' "$PRIVATE/fixture.json"
  printf 'Open the pairing screen in separate browser profiles for kiosk and staff. Press Ctrl-C here to remove only this disposable demo.\n'
  wait "$NEXT_PID"
  exit 0
fi
(cd companion && node tests/rehearsal/browser.cjs) 2>&1 | tee rehearsal-evidence/browser.log
(cd companion && node tests/rehearsal/hub.cjs) 2>&1 | tee rehearsal-evidence/hub-browser.log
(cd companion && node tests/rehearsal/hub-feedback.cjs) 2>&1 | tee rehearsal-evidence/hub-feedback-browser.log
attendance=$(docker exec "$DB" psql -U odoo -d dojang_demo_rehearsal -Atc 'SELECT count(*) FROM dojo_attendance_log;')
reviews=$(docker exec "$DB" psql -U odoo -d dojang_demo_rehearsal -Atc 'SELECT count(*) FROM dojo_companion_review_receipt;')
followups=$(docker exec "$DB" psql -U odoo -d dojang_demo_rehearsal -Atc "SELECT count(*) FROM dojo_companion_followup WHERE state = 'approved';")
hub_messages=$(docker exec "$DB" psql -U odoo -d dojang_demo_rehearsal -Atc "SELECT count(*) FROM dojo_hub_message WHERE state = 'resolved' AND summary IS NOT NULL;")
hub_bookings=$(docker exec "$DB" psql -U odoo -d dojang_demo_rehearsal -Atc "SELECT count(*) FROM dojo_class_enrollment e JOIN dojo_class_session s ON s.id = e.session_id WHERE s.start_datetime > now() AND e.status = 'registered';")
checkouts=$(docker exec "$DB" psql -U odoo -d dojang_demo_rehearsal -Atc "SELECT count(*) FROM dojo_attendance_log WHERE checkout_datetime IS NOT NULL;")
test "$attendance" = 2
test "$reviews" = 1
test "$followups" = 1
test "$hub_messages" = 1
test "$hub_bookings" = 1
test "$checkouts" = 1
printf 'attendance_rows=%s\nreview_receipts=%s\napproved_followups=%s\n' "$attendance" "$reviews" "$followups" > rehearsal-evidence/database-verification.txt
printf 'resolved_hub_messages=%s\nfuture_class_bookings=%s\ncheckouts=%s\n' "$hub_messages" "$hub_bookings" "$checkouts" >> rehearsal-evidence/database-verification.txt
docker logs "$ODOO" > rehearsal-evidence/odoo-http.log 2>&1
printf 'PASS: browser, server gateway and Odoo database agree. Synthetic data only.\n' > rehearsal-evidence/rehearsal-result.txt
