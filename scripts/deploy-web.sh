#!/bin/sh
# Usage: deploy-web.sh [web|car|dashboard]   (default: web)
# Uploads clients/<service>/dist with the Linux StaticSitesClient in Docker. Use this where the SWA CLI
# cannot run its client, such as Apple Silicon without Rosetta. The deployment token stays in memory.
# The Static Web App is found by its azd-service-name tag, never by its position in a list.
set -eu
service="${1:-web}"
case "$service" in
  web | car | dashboard) ;;
  *) echo "deploy-web: unknown service '$service'; use web, car or dashboard" >&2; exit 1 ;;
esac
cd "$(dirname "$0")/.."
if [ ! -d "clients/$service/dist" ]; then
  echo "deploy-web: clients/$service/dist is missing; build the $service client first" >&2
  exit 1
fi
eval "$(azd env get-values | grep -E '^AZURE_RESOURCE_GROUP=')"
sites="$(az staticwebapp list -g "$AZURE_RESOURCE_GROUP" --query '[].[name, tags."azd-service-name"]' -o tsv)"
matches="$(printf '%s\n' "$sites" | awk -F '\t' -v s="$service" '$2 == s { print $1 }')"
if [ -z "$matches" ]; then
  echo "deploy-web: no Static Web App in $AZURE_RESOURCE_GROUP has the tag azd-service-name=$service" >&2
  exit 1
fi
if [ "$(printf '%s\n' "$matches" | wc -l)" -gt 1 ]; then
  echo "deploy-web: more than one Static Web App in $AZURE_RESOURCE_GROUP has the tag azd-service-name=$service" >&2
  exit 1
fi
name="$matches"
TOKEN="$(az staticwebapp secrets list -n "$name" -g "$AZURE_RESOURCE_GROUP" --query properties.apiKey -o tsv)"
export TOKEN
docker run --rm --platform linux/amd64 -e TOKEN -e DEPLOYMENT_PROVIDER=SwaCli -e REPOSITORY_BASE=/ \
  -v "$PWD/clients/$service/dist:/app/dist:ro" -w /admin \
  mcr.microsoft.com/appsvc/staticappsclient:stable \
  sh -c '/bin/staticsites/StaticSitesClient upload --workdir /app --app /app/dist --skipAppBuild true --apiToken "$TOKEN"'
