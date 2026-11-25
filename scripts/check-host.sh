#!/bin/sh
# Print the DNS records that a host name of our own needs, and check if they are there.
#
#   scripts/check-host.sh api api.example.com
#   scripts/check-host.sh dealer dealer.example.com
#
# It changes nothing: the owner of the domain makes the records. When they are there it prints the two commands that
# bind the name. The name stays in the azd environment, so a later `azd provision` keeps it.
# Needs dig, and an azd environment that has been provisioned. Exit code 0 when every record is right, 1 otherwise.
set -eu
cd "$(dirname "$0")/.."

service="${1:-}"
host="${2:-}"
case "$service" in
  api) setting=API_HOST ;;
  dealer) setting=DEALER_HOST ;;
  *) echo "usage: scripts/check-host.sh api|dealer <host>" >&2; exit 2 ;;
esac
case "$host" in
  *[!a-z0-9.-]* | .* | *. | -* | "") echo "check-host: a host name in lower case, as in api.example.com" >&2; exit 2 ;;
  *.*.*) ;; # a name directly under a top level domain has no CNAME
  *) echo "check-host: the host must be a subdomain, as in api.example.com" >&2; exit 2 ;;
esac
# The address that Azure gave the app, also when it has a name of our own.
address="$(azd env get-value "${setting%_HOST}_URL")"
verification="$(azd env get-value HOST_VERIFICATION_ID)"
target="${address#https://}"

echo "Records for $host:"
echo "  CNAME  $host        $target"
echo "  TXT    asuid.$host  $verification"
echo

ok=true
good() { echo "ok       $1"; }
missing() { echo "missing  $1"; ok=false; }

# The CNAME must point straight at the app. One in between stops the certificate.
found="$(dig +short CNAME "$host" | sed 's/\.$//')"
if [ "$found" = "$target" ]; then good CNAME; else missing "CNAME (found: ${found:-none})"; fi

if dig +short TXT "asuid.$host" | tr -d '"' | grep -qxiF "$verification"; then good TXT; else missing TXT; fi

# The nearest CAA record, from the host and up, decides who may issue a certificate. None: anyone may.
name="$host"
caa=""
while [ -z "$caa" ]; do
  caa="$(dig +short CAA "$name" | grep '^[0-9]' || true)"
  case "$name" in *.*) name="${name#*.}" ;; *) break ;; esac
done
if [ -z "$caa" ] || echo "$caa" | grep -qi 'issue "digicert\.com'; then
  good CAA
else
  missing 'CAA (there is one, and it must also have: 0 issue "digicert.com")'
fi

echo
[ "$ok" = true ] || { echo "Not ready. A new record can take an hour to show."; exit 1; }
echo "Ready. Next:"
echo "  azd env set $setting $host"
echo "  azd provision"
