#!/bin/sh
set -eu
# Straight from off to active would point the API and the agents at a service that is not there.
if [ "${SEARCH_SERVERLESS:-off}" = active ] && [ -z "${SEARCH_SERVERLESS_ENDPOINT:-}" ]; then
  echo "preprovision: SEARCH_SERVERLESS is active, but there is no Serverless service. Set it to standby, provision and fill the service first" >&2
  exit 1
fi
DOMAIN="${VOLTRIEVE_VERIFIED_DOMAIN:-$(az rest -m GET -u 'https://graph.microsoft.com/v1.0/domains' --query "value[?isDefault].id | [0]" -o tsv)}"
if [ -z "$DOMAIN" ]; then
  echo "preprovision: could not determine the verified domain; set VOLTRIEVE_VERIFIED_DOMAIN" >&2
  exit 1
fi
# The three URLs exist from the second provision on; the first one is covered by set-spa-redirect.
# Setup replaces each registration's redirect URIs, so a known URL is passed again here.
uv run --project infra/scripts/entra voltrieve-entra setup --verified-domain "$DOMAIN" \
  ${WEB_URL:+--web-redirect-uri "$WEB_URL"} \
  ${CAR_URL:+--car-redirect-uri "$CAR_URL"} \
  ${DASHBOARD_URL:+--dashboard-redirect-uri "$DASHBOARD_URL"}
