#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
az bicep build --file main.bicep --stdout > /dev/null
az bicep lint --file main.bicep
# every role assignment uses a GUID, never a role name
if grep -rn "roleDefinitionId.*'[A-Z][a-zA-Z ]*'" modules/ main.bicep; then echo "role name used"; exit 1; fi
# the only key lookups allowed are the commented, non-application ones
if grep -rn -i "listKeys\|apiKey\|accountKey" modules/ main.bicep | grep -v "not an application secret"; then echo "key usage found"; exit 1; fi
# the API emits GenAI spans, and nothing reads the knowledge base connection name at runtime
grep -q "AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING" modules/api-app.bicep || { echo "api-app lacks the GenAI tracing setting"; exit 1; }
if grep -q "KB_CONNECTION_NAME\|kbConnectionName" modules/api-app.bicep; then echo "api-app still takes the knowledge base connection name"; exit 1; fi
# three browser apps, each a Free Static Web App tagged for azd by a required serviceName
grep -q "^param serviceName string$" modules/static-web-app.bicep || { echo "static-web-app lacks a required serviceName"; exit 1; }
grep -q "'azd-service-name': serviceName" modules/static-web-app.bicep || { echo "static-web-app does not tag by serviceName"; exit 1; }
if grep -q "'azd-service-name': 'web'" modules/static-web-app.bicep; then echo "static-web-app hard-codes the web tag"; exit 1; fi
for svc in web car dashboard; do
  grep -q "serviceName: '$svc'" main.bicep || { echo "main.bicep lacks the $svc static web app"; exit 1; }
done
[ "$(grep -c "modules/static-web-app.bicep" main.bicep)" = 3 ] || { echo "expected three static web apps"; exit 1; }
grep -q "name: 'stapp-voltrieve-\${environmentName}'" main.bicep || { echo "web app name changed"; exit 1; }
grep -q "stapp-voltrieve-car-" main.bicep && grep -q "stapp-voltrieve-dashboard-" main.bicep || { echo "car or dashboard name missing"; exit 1; }
for out in WEB_URL CAR_URL DASHBOARD_URL; do
  grep -q "^output $out string" main.bicep || { echo "main.bicep lacks output $out"; exit 1; }
done
grep -q "param promptAgentCar string" modules/api-app.bicep || { echo "api-app lacks promptAgentCar"; exit 1; }
grep -q "PROMPT_AGENT_CAR" modules/api-app.bicep || { echo "api-app lacks PROMPT_AGENT_CAR"; exit 1; }
grep -q "promptAgentCar: 'voltrieve-prompt-car'" main.bicep || { echo "main.bicep lacks the car prompt agent"; exit 1; }
for origin in "web.outputs.url" "car.outputs.url" "dashboard.outputs.url" "localhost:5173" "localhost:5174" "localhost:5175"; do
  grep "corsOrigins:" main.bicep | grep -q "$origin" || { echo "CORS lacks $origin"; exit 1; }
done
# search: one management version everywhere, and one value chooses the planning model
if grep -rn "Microsoft.Search/searchServices@" modules/ | grep -v "@2026-11-01'"; then echo "another search management version"; exit 1; fi
grep -q "^param planningModelName string = 'gpt-6-luna'$" main.bicep || { echo "planning model default changed"; exit 1; }
grep -q '"planningModelName": { "value": "${PLANNING_MODEL_NAME=gpt-6-luna}" }' main.parameters.json || { echo "planning model is not one azd value"; exit 1; }
if grep -q "planningModelVersion" main.bicep modules/foundry.bicep; then echo "going back must not need a version too"; exit 1; fi
for model in "{ name: 'gpt-5.4-mini', version: '2026-03-17' }" "{ name: 'gpt-6-luna', version: '2026-09-22' }"; do
  grep -qF "$model" main.bicep || { echo "main.bicep lacks planning deployment $model"; exit 1; }
done
# Serverless search is off by default, and the Basic service is never conditional
grep -q "^param serverlessSearch string = 'off'$" main.bicep || { echo "Serverless search is not off by default"; exit 1; }
grep -q '"serverlessSearch": { "value": "${SEARCH_SERVERLESS=off}" }' main.parameters.json || { echo "SEARCH_SERVERLESS does not default to off"; exit 1; }
grep -q "^module search 'modules/search.bicep' = {$" main.bicep || { echo "the Basic service became conditional"; exit 1; }
grep -q "sku: { name: 'basic' }" modules/search.bicep || { echo "the Basic service changed tier"; exit 1; }
grep -q "sku: { name: 'serverless' }" modules/search-serverless.bicep || { echo "no Serverless service"; exit 1; }
for f in modules/search.bicep modules/search-serverless.bicep; do
  grep -q "disableLocalAuth: true" $f || { echo "$f allows keys"; exit 1; }
  grep -q "knowledgeRetrieval: 'standard'" $f || { echo "$f has the free retrieval plan"; exit 1; }
done
# what is compiled: off creates one service, and the Serverless one hangs on the parameter
az bicep build --file main.bicep --stdout 2>/dev/null | python3 -c '
import json, sys
t = json.load(sys.stdin)
r = t["resources"]
r = list(r.values()) if isinstance(r, dict) else r
conditions = {x["name"]: x.get("condition") for x in r if x["type"].endswith("/deployments")}
serverless = {n: c for n, c in conditions.items() if "serverless" in n.lower()}
assert len(serverless) == 2, sorted(conditions)
assert all(c and "deployServerless" in c for c in serverless.values()), serverless
assert t["parameters"]["serverlessSearch"]["allowedValues"] == ["off", "standby", "active"]
assert t["parameters"]["planningModelName"]["allowedValues"] == ["gpt-6-luna", "gpt-5.4-mini"]
' || { echo "the Serverless service is not behind its parameter"; exit 1; }
echo "bicep ok"
