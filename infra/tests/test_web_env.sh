#!/bin/sh
# The web-prepackage hook must write every variable config.ts requires, and only variables
# config.ts reads (the dashboard links are optional).
set -eu
cd "$(dirname "$0")/../.."
required="$(sed -n 's/^const REQUIRED = \[\(.*\)\] as const;/\1/p' clients/web/src/config.ts | tr -d '" ' | tr ',' '\n' | sort)"
written="$(grep -o 'VITE_[A-Z_]*=' hooks/web-prepackage.sh | tr -d '=' | sort -u)"
[ -n "$required" ] || { echo "could not read REQUIRED from config.ts" >&2; exit 1; }
for name in $required; do
  echo "$written" | grep -qx "$name" || { echo "hook does not write $name" >&2; exit 1; }
done
for name in $written; do
  grep -q "$name" clients/web/src/config.ts || { echo "hook writes $name, which config.ts never reads" >&2; exit 1; }
done
echo "web env names ok"
