#!/bin/sh
set -eu
# Straight from off to active would point the API and the agents at a service that is not there.
if [ "${SEARCH_SERVERLESS:-off}" = active ] && [ -z "${SEARCH_SERVERLESS_ENDPOINT:-}" ]; then
  echo "preprovision: SEARCH_SERVERLESS is active, but there is no Serverless service. Set it to standby, provision and fill the service first" >&2
  exit 1
fi
# Without Basic everything has to use the Serverless service. infra/main.bicep refuses it too.
case "${SEARCH_BASIC:-on}" in
  on) ;;
  off)
    if [ "${SEARCH_SERVERLESS:-off}" != active ]; then
      echo "preprovision: SEARCH_BASIC is off, which needs SEARCH_SERVERLESS active, because nothing else would answer searches. Set SEARCH_BASIC to on, or make the Serverless service active first" >&2
      exit 1
    fi ;;
  *)
    echo "preprovision: SEARCH_BASIC is on or off" >&2
    exit 1 ;;
esac
DOMAIN="${VOLTRIEVE_VERIFIED_DOMAIN:-$(az rest -m GET -u 'https://graph.microsoft.com/v1.0/domains' --query "value[?isDefault].id | [0]" -o tsv)}"
if [ -z "$DOMAIN" ]; then
  echo "preprovision: could not determine the verified domain; set VOLTRIEVE_VERIFIED_DOMAIN" >&2
  exit 1
fi
# The URLs of the four browser apps exist from the second provision on; the first one is covered by set-spa-redirect.
# Setup replaces each registration's redirect URIs, so a known URL is passed again here.
uv run --project infra/scripts/entra voltrieve-entra setup --verified-domain "$DOMAIN" \
  ${WEB_URL:+--web-redirect-uri "$WEB_URL"} \
  ${CAR_URL:+--car-redirect-uri "$CAR_URL"} \
  ${DASHBOARD_URL:+--dashboard-redirect-uri "$DASHBOARD_URL"} \
  ${DEALER_URL:+--dealer-redirect-uri "$DEALER_URL"}
