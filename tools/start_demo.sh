#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
command -v docker >/dev/null
command -v node >/dev/null
node -e 'if(Number(process.versions.node.split(".")[0])<22)process.exit(1)'
docker info >/dev/null
PIN=d40b7c8c5de81ce8d1528a6b31608468f5f30d4d
if [ ! -d odoo ]; then
  git init odoo
  git -C odoo fetch --depth=1 https://github.com/odoo/odoo.git "$PIN"
  git -C odoo checkout --detach FETCH_HEAD
fi
test "$(git -C odoo rev-parse HEAD)" = "$PIN"
(cd companion && npm ci)
export DOJANG_INTERACTIVE_DEMO=1
bash tools/run_rehearsal.sh
