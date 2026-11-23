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
echo "bicep ok"
