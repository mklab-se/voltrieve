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
for value in "${MCP_DOORS:-false}" "${MCP_ALLOW_CLI:-false}"; do
  case "$value" in
    true | false) ;;
    *)
      echo "preprovision: MCP_DOORS and MCP_ALLOW_CLI are true or false" >&2
      exit 1 ;;
  esac
done
DOMAIN="${VOLTRIEVE_VERIFIED_DOMAIN:-$(az rest -m GET -u 'https://graph.microsoft.com/v1.0/domains' --query "value[?isDefault].id | [0]" -o tsv)}"
if [ -z "$DOMAIN" ]; then
  echo "preprovision: could not determine the verified domain; set VOLTRIEVE_VERIFIED_DOMAIN" >&2
  exit 1
fi
# The MCP doors are set up only when they are asked for.
set --
if [ "${MCP_DOORS:-false}" = true ]; then
  set -- --mcp
  [ "${MCP_ALLOW_CLI:-false}" = false ] || set -- "$@" --allow-cli
  [ -z "${API_HOST:-}" ] || set -- "$@" --api-host "$API_HOST"
  [ -z "${DEALER_HOST:-}" ] || set -- "$@" --dealer-host "$DEALER_HOST"
  # Setup checks the list, and begins one with the deployer when there is none.
  [ -z "${DEALER_DOOR_CITIES:-}" ] || set -- "$@" --dealer-cities "$DEALER_DOOR_CITIES"
fi
# The URLs of the four browser apps exist from the second provision on; the first one is covered by set-spa-redirect.
# Setup replaces each registration's redirect URIs, so a known URL is passed again here.
uv run --project infra/scripts/entra voltrieve-entra setup --verified-domain "$DOMAIN" \
  ${WEB_URL:+--web-redirect-uri "$WEB_URL"} \
  ${CAR_URL:+--car-redirect-uri "$CAR_URL"} \
  ${DASHBOARD_URL:+--dashboard-redirect-uri "$DASHBOARD_URL"} \
  ${DEALER_URL:+--dealer-redirect-uri "$DEALER_URL"} \
  "$@"
