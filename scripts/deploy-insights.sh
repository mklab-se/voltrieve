#!/bin/sh
# Build the image of the usage insights job and give it to the job.
#
#   scripts/deploy-insights.sh
#
# 1. az acr build           the linux/amd64 image, with a unique tag (repository root as context)
# 2. azd env set            INSIGHTS_IMAGE, so a later `azd provision` keeps this image
# 3. az containerapp job    the job runs this image from its next start
# It starts no run and changes no schedule. Run it after `azd provision`, from the repository root,
# signed in with az and azd as the deployer. It needs no secrets.
set -eu
cd "$(dirname "$0")/.."

load_env() { set -a; eval "$(azd env get-values)"; set +a; }

load_env
: "${AZURE_RESOURCE_GROUP:?deploy-insights: run azd provision first}"
: "${AZURE_CONTAINER_REGISTRY_NAME:?deploy-insights: run azd provision first}"
: "${AZURE_CONTAINER_REGISTRY_ENDPOINT:?deploy-insights: run azd provision first}"
: "${INSIGHTS_JOB_NAME:?deploy-insights: run azd provision first}"

tag="$(date -u +%Y%m%d%H%M%S)-$(git rev-parse --short HEAD)"
repository="voltrieve/insights"
image="$AZURE_CONTAINER_REGISTRY_ENDPOINT/$repository:$tag"

az acr build -r "$AZURE_CONTAINER_REGISTRY_NAME" -t "$repository:$tag" --platform linux/amd64 \
  -f services/insights/Dockerfile .
azd env set INSIGHTS_IMAGE "$image"
az containerapp job update -n "$INSIGHTS_JOB_NAME" -g "$AZURE_RESOURCE_GROUP" --image "$image" --output none

echo "Insights job deployed: $INSIGHTS_JOB_NAME runs $image from its next start"
