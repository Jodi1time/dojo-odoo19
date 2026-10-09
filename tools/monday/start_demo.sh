#!/usr/bin/env bash
# Launch a fresh local synthetic demo from committed code. No hosted credentials.
set -euo pipefail
if [[ "${1:-}" == "--help" ]]; then
  printf '%s\n' 'Usage: bash tools/monday/start_demo.sh' 'Requires Linux/WSL, Docker, Git, Node 22, npm, Python 3 and free localhost ports 3000/8069.' 'Creates a separate temporary working copy and synthetic database. Ctrl+C stops only these generated containers.'
  exit 0
fi
for tool in docker git node npm python3 openssl curl tar; do command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool" >&2; exit 1; }; done
docker info >/dev/null
node -e 'if(Number(process.versions.node.split(".")[0])<22)process.exit(1)' || { echo 'Node 22 or later is required.' >&2; exit 1; }
python3 - <<'PY'
import socket
for port in (3000,8069):
    with socket.socket() as s:
        try:s.bind(('127.0.0.1',port))
        except OSError:raise SystemExit(f'Local port {port} is already in use. Stop that application first; it will not be modified.')
PY
ROOT="$(git -C "$(dirname "$0")/../.." rev-parse --show-toplevel)"
REV="$(git -C "$ROOT" rev-parse HEAD)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/dojang-demo-XXXXXXXX")"
chmod 700 "$WORK"
git -C "$ROOT" archive "$REV" | tar -x -C "$WORK"
test -f "$WORK/companion/package-lock.json" || { echo 'Use the materialized release/monday-demo branch, not an earlier overlay commit.' >&2; exit 1; }
cd "$WORK"
printf '%s\n' "$REV" > source-commit.txt
mkdir -p .private/odoo-data logs
chmod 777 .private .private/odoo-data
IDENT="$(openssl rand -hex 6)"
NET="dojang-demo-$IDENT"; DB="dojang-demo-db-$IDENT"; APP="dojang-demo-odoo-$IDENT"; IMAGE="dojang-monday-local-$IDENT"
PASSWORD="$(openssl rand -hex 24)"
cleanup(){ docker rm -f "$APP" "$DB" >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true; printf '\nDemo stopped. Source and private setup files remain at %s\n' "$WORK"; }
trap cleanup EXIT INT TERM
printf 'Preparing committed source %s in %s\n' "$REV" "$WORK"
(cd companion && npm ci --ignore-scripts --no-audit --no-fund && npm run test:integration && npx next typegen && npx tsc --noEmit) > logs/frontend-checks.log 2>&1
git init odoo >/dev/null
git -C odoo fetch --depth=1 https://github.com/odoo/odoo.git d40b7c8c5de81ce8d1528a6b31608468f5f30d4d
git -C odoo checkout --detach FETCH_HEAD
docker build -t "$IMAGE" . > logs/build-odoo.log 2>&1
docker network create --internal "$NET" >/dev/null
docker run -d --name "$DB" --network "$NET" -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD="$PASSWORD" -e POSTGRES_DB=postgres postgres:16 >/dev/null
for attempt in $(seq 1 60); do if docker exec "$DB" pg_isready -U odoo >/dev/null 2>&1; then break; fi; sleep 2; done
docker exec "$DB" pg_isready -U odoo
ARGS=(--db_host="$DB" --db_user=odoo --db_password="$PASSWORD" -d dojang_demo_local --db-filter='^dojang_demo_local$' --addons-path=/opt/odoo/odoo/addons,/opt/odoo/addons,/mnt/extra-addons --max-cron-threads=0)
MOUNTS=(-v "$WORK/addons:/mnt/extra-addons:ro" -v "$WORK/.private/odoo-data:/var/lib/odoo")
docker run --rm --network "$NET" "${MOUNTS[@]}" "$IMAGE" "${ARGS[@]}" -i dojo_kiosk,dojo_credits --without-demo=all --stop-after-init --test-enable --test-tags=/dojo_kiosk:TestKioskSessionFirstV2 > logs/native-tests.log 2>&1
python3 - <<'PY'
import ast,re,pathlib
names=[n.name for n in ast.walk(ast.parse(pathlib.Path('addons/dojo_kiosk/tests/test_kiosk_v2.py').read_text())) if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')]
s=pathlib.Path('logs/native-tests.log').read_text(errors='replace')
assert all(re.search(r'Starting .*TestKioskSessionFirstV2\.'+re.escape(n)+r'\b',s) for n in names),'Native tests missing'
summary=re.findall(r'(\d+) failed, (\d+) error\(s\) of (\d+) tests',s)
assert summary and all(int(f)==0 and int(e)==0 for f,e,n in summary) and max(int(n) for f,e,n in summary)>=len(names),'Native test failures'
PY
docker run --rm -i --network "$NET" "${MOUNTS[@]}" -v "$WORK/.private:/private" -e DOJANG_ENV_OUTPUT=/private/.env.local -e DOJANG_PUBLIC_ORIGIN=http://localhost:3000 "$IMAGE" shell "${ARGS[@]}" --no-http < tools/monday/seed.py > logs/seed.log 2>&1
docker run --rm --user 0 --entrypoint sh -v "$WORK/.private:/private" "$IMAGE" -c 'chown "$1:$2" /private/.env.local /private/fixture.json' -- "$(id -u)" "$(id -g)"
cp .private/.env.local companion/.env.local
chmod 600 companion/.env.local
python3 - <<'PY'
import os,pathlib
p=pathlib.Path('.private');env=dict(line.split('=',1) for line in (p/'.env.local').read_text().splitlines() if '=' in line)
for role in ('kiosk','staff'):
    fd=os.open(p/(role+'-pairing.txt'),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:f.write(env['DOJANG_'+role.upper()+'_PAIR_KEY']+'\n')
PY
docker run -d --name "$APP" --network "$NET" -p 127.0.0.1:8069:8069 "${MOUNTS[@]}" "$IMAGE" "${ARGS[@]}" --http-interface=0.0.0.0 >/dev/null
for attempt in $(seq 1 60); do if curl -s -o /dev/null http://127.0.0.1:8069/web/login; then break; fi; sleep 2; done
(cd companion && npm run build) > logs/build-next.log 2>&1
printf '\nSynthetic demo: http://localhost:3000/integration/pair\n'
printf 'Private kiosk pairing key file: %s/.private/kiosk-pairing.txt\n' "$WORK"
printf 'Private staff pairing key file: %s/.private/staff-pairing.txt\n' "$WORK"
printf '%s\n' 'Pair a normal browser as kiosk and a separate browser profile as staff. Do not share keys in screenshots or messages.' 'The guided Companion is rules-based and creates a reviewed internal Odoo task. No email, SMS, charge or rank change is enabled.' 'Keep this terminal and the kiosk page open. Ctrl+C stops this synthetic environment.'
cd companion
npm run start -- --hostname 127.0.0.1 --port 3000
