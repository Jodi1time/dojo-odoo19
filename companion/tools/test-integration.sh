#!/usr/bin/env bash
set -euo pipefail
npx tsc -p tsconfig.integration.json
node --test tests/*.test.cjs
