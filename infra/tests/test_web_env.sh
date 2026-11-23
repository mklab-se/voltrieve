#!/bin/sh
# Each prepackage hook must write every variable its app's config.ts requires, and only variables
# that config.ts reads (the portal links of the dashboard are optional).
set -eu
cd "$(dirname "$0")/../.."
check() {
  app="$1"
  hook="$2"
  required="$(sed -n 's/^const REQUIRED = \[\(.*\)\] as const;/\1/p' "clients/$app/src/config.ts" | tr -d '" ' | tr ',' '\n' | sort)"
  written="$(grep -o 'VITE_[A-Z_]*=' "$hook" | tr -d '=' | sort -u)"
  [ -n "$required" ] || { echo "could not read REQUIRED from clients/$app/src/config.ts" >&2; exit 1; }
  for name in $required; do
    echo "$written" | grep -qx "$name" || { echo "$hook does not write $name" >&2; exit 1; }
  done
  for name in $written; do
    grep -q "$name" "clients/$app/src/config.ts" || { echo "$hook writes $name, which clients/$app/src/config.ts never reads" >&2; exit 1; }
  done
  echo "$app env names ok"
}
check web hooks/web-prepackage.sh
check dashboard hooks/dashboard-prepackage.sh
