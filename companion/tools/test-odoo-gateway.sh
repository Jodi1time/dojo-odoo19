#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
cd "$ROOT"
./node_modules/.bin/tsc --strict --target ES2022 --module commonjs --moduleResolution node --lib ES2022,DOM --types node --typeRoots ./node_modules/@types --outDir "$BUILD" src/server/odoo-gateway.ts src/server/companion-live.ts src/server/kiosk-recovery.ts src/domain/kiosk/attendanceOutbox.ts src/contracts/kiosk-attendance.ts src/domain/companion.ts
export DOJANG_GATEWAY_MODULE="$BUILD/server/odoo-gateway.js"
export DOJANG_COMPANION_MODULE="$BUILD/server/companion-live.js"
export DOJANG_OUTBOX_MODULE="$BUILD/domain/kiosk/attendanceOutbox.js"
node --test tests/odoo-gateway.test.cjs tests/companion-live.test.cjs tests/attendance-outbox.test.cjs
