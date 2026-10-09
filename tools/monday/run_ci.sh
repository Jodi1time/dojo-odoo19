#!/usr/bin/env bash
set -euo pipefail
ROOT="$PWD"
mkdir -p readiness-results .private
chmod 777 .private
export ODOO_COMMIT=d40b7c8c5de81ce8d1528a6b31608468f5f30d4d
NET="dojang-monday-${GITHUB_RUN_ID:-local}"
DB="dojang-monday-db-${GITHUB_RUN_ID:-local}"
APP="dojang-monday-odoo-${GITHUB_RUN_ID:-local}"
PASSWORD="$(openssl rand -hex 24)"
echo "::add-mask::$PASSWORD"
cleanup(){ docker rm -f "$APP" "$DB" >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true; }
trap cleanup EXIT
git init odoo >/dev/null
git -C odoo fetch --depth=1 https://github.com/odoo/odoo.git "$ODOO_COMMIT"
git -C odoo checkout --detach FETCH_HEAD
test "$(git -C odoo rev-parse HEAD)" = "$ODOO_COMMIT"
docker build -t dojang-monday-test . > readiness-results/build-odoo.log 2>&1
docker network create --internal "$NET" >/dev/null
docker run -d --name "$DB" --network "$NET" -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD="$PASSWORD" -e POSTGRES_DB=postgres postgres:16 >/dev/null
for attempt in $(seq 1 60); do if docker exec "$DB" pg_isready -U odoo >/dev/null 2>&1; then break; fi; sleep 2; done
docker exec "$DB" pg_isready -U odoo
ARGS=(--db_host="$DB" --db_user=odoo --db_password="$PASSWORD" -d dojang_demo_ci --db-filter='^dojang_demo_ci$' --addons-path=/opt/odoo/odoo/addons,/opt/odoo/addons,/mnt/extra-addons --max-cron-threads=0)
docker run --rm --network "$NET" -v "$ROOT/addons:/mnt/extra-addons:ro" dojang-monday-test "${ARGS[@]}" -i dojo_kiosk,dojo_credits --without-demo=all --stop-after-init --test-enable --test-tags=/dojo_kiosk:TestKioskSessionFirstV2 > readiness-results/native-tests.log 2>&1
python - <<'PY'
import ast,pathlib,re
p=pathlib.Path('addons/dojo_kiosk/tests/test_kiosk_v2.py');names=[n.name for n in ast.walk(ast.parse(p.read_text())) if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')]
s=pathlib.Path('readiness-results/native-tests.log').read_text(errors='replace')
missing=[n for n in names if not re.search(r'Starting .*TestKioskSessionFirstV2\.'+re.escape(n)+r'\b',s)]
summary=re.findall(r'(\d+) failed, (\d+) error\(s\) of (\d+) tests',s)
assert not missing,missing
assert summary and all(int(f)==0 and int(e)==0 for f,e,n in summary) and max(int(n) for f,e,n in summary)>=len(names),summary
pathlib.Path('readiness-results/native-gate.txt').write_text(f'PASS: {len(names)} native Odoo regression tests executed.\n')
PY
docker run --rm -i --network "$NET" --user "$(id -u):$(id -g)" -e HOME=/tmp -e DOJANG_ENV_OUTPUT=/private/.env.local -e DOJANG_PUBLIC_ORIGIN=http://localhost:3000 -v "$ROOT/.private:/private" -v "$ROOT/addons:/mnt/extra-addons:ro" dojang-monday-test shell "${ARGS[@]}" --data-dir=/tmp/odoo-seed --no-http < tools/monday/seed.py > readiness-results/seed.log 2>&1
cp .private/.env.local companion/.env.local
chmod 600 companion/.env.local
docker run -d --name "$APP" --network "$NET" -p 127.0.0.1:8069:8069 -v "$ROOT/addons:/mnt/extra-addons:ro" dojang-monday-test "${ARGS[@]}" --http-interface=0.0.0.0 >/dev/null
for attempt in $(seq 1 60); do if curl -s -o /dev/null http://127.0.0.1:8069/web/login; then break; fi; sleep 2; done
cd companion
npm run test:integration > ../readiness-results/gateway-tests.log 2>&1
npx next typegen > ../readiness-results/typegen.log 2>&1
npx tsc --noEmit > ../readiness-results/typecheck.log 2>&1
npm run build > ../readiness-results/build-next.log 2>&1
npm run start > ../readiness-results/next-runtime.log 2>&1 & NEXT_PID=$!
for attempt in $(seq 1 60); do if curl -fsS -o /dev/null http://localhost:3000/integration/pair; then break; fi; sleep 2; done
npx playwright install --with-deps chromium > ../readiness-results/playwright-install.log 2>&1
npm run test:e2e > ../readiness-results/browser-tests.log 2>&1
kill "$NEXT_PID" || true
cd "$ROOT"
docker exec "$DB" psql -U odoo -d dojang_demo_ci -Atc "SELECT 'attendance=' || count(*) FROM dojo_attendance_log; SELECT 'outbox=' || count(*) FROM dojo_kiosk_outbox; SELECT 'companion_tasks=' || count(*) FROM dojo_companion_action_receipt;" > readiness-results/database-readback.txt
python - <<'PY'
from pathlib import Path
s=Path('readiness-results/database-readback.txt').read_text()
assert 'attendance=2' in s and 'outbox=2' in s and 'companion_tasks=1' in s,s
Path('readiness-results/release-gate.txt').write_text('PASS: native Odoo, gateway tests, strict TypeScript, Next production build, and browser rehearsal against a real synthetic Odoo database. Hosted preview and generative AI provider are NOT verified by this gate.\n')
PY
