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
grep -q "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT', value: 'false'" modules/api-app.bicep || { echo "api-app may record message content"; exit 1; }
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
for c in CAR WEB CLI; do
  grep -q "name: '${c}_CLIENT_ID'" modules/api-app.bicep || { echo "api-app lacks ${c}_CLIENT_ID"; exit 1; }
  grep -q "\"$(echo $c | tr A-Z a-z)ClientId\": { \"value\": \"\${${c}_CLIENT_ID=}\" }" main.parameters.json || { echo "main.parameters.json lacks ${c}_CLIENT_ID"; exit 1; }
done
grep -q "carClientId: carClientId" main.bicep && grep -q "webClientId: webClientId" main.bicep && grep -q "cliClientId: cliClientId" main.bicep || { echo "main.bicep does not pass the client ids on"; exit 1; }
grep -q "param promptAgentCar string" modules/api-app.bicep || { echo "api-app lacks promptAgentCar"; exit 1; }
grep -q "PROMPT_AGENT_CAR" modules/api-app.bicep || { echo "api-app lacks PROMPT_AGENT_CAR"; exit 1; }
grep -q "promptAgentCar: 'voltrieve-prompt-car'" main.bicep || { echo "main.bicep lacks the car prompt agent"; exit 1; }
for origin in "web.outputs.url" "car.outputs.url" "dashboard.outputs.url" "localhost:5173" "localhost:5174" "localhost:5175"; do
  grep "corsOrigins:" main.bicep | grep -q "$origin" || { echo "CORS lacks $origin"; exit 1; }
done
# search: one management version everywhere, and one value chooses the planning model
if grep -rn "Microsoft.Search/searchServices@" modules/ | grep -v "@2026-11-01'"; then echo "another search management version"; exit 1; fi
grep -q "^param planningModelName string = 'gpt-5.4-mini'$" main.bicep || { echo "a plain provision must not change the planning model"; exit 1; }
grep -q '"planningModelName": { "value": "${PLANNING_MODEL_NAME=gpt-5.4-mini}" }' main.parameters.json || { echo "planning model is not one azd value"; exit 1; }
if grep -q "planningModelVersion" main.bicep modules/foundry.bicep; then echo "going back must not need a version too"; exit 1; fi
for model in "{ name: 'gpt-5.4-mini', version: '2026-03-17' }" "{ name: 'gpt-6-luna', version: '2026-09-22' }"; do
  grep -qF "$model" main.bicep || { echo "main.bicep lacks planning deployment $model"; exit 1; }
done
# Serverless search is off and the Basic service on by default
grep -q "^param serverlessSearch string = 'off'$" main.bicep || { echo "Serverless search is not off by default"; exit 1; }
grep -q '"serverlessSearch": { "value": "${SEARCH_SERVERLESS=off}" }' main.parameters.json || { echo "SEARCH_SERVERLESS does not default to off"; exit 1; }
grep -q "^param basicSearch string = 'on'$" main.bicep || { echo "the Basic service is not on by default"; exit 1; }
grep -q '"basicSearch": { "value": "${SEARCH_BASIC=on}" }' main.parameters.json || { echo "SEARCH_BASIC does not default to on"; exit 1; }
grep -q "sku: { name: 'basic' }" modules/search.bicep || { echo "the Basic service changed tier"; exit 1; }
grep -q "sku: { name: 'serverless' }" modules/search-serverless.bicep || { echo "no Serverless service"; exit 1; }
for f in modules/search.bicep modules/search-serverless.bicep; do
  grep -q "disableLocalAuth: true" $f || { echo "$f allows keys"; exit 1; }
  grep -q "knowledgeRetrieval: 'standard'" $f || { echo "$f has the free retrieval plan"; exit 1; }
done
# what is compiled: what hangs on the two parameters, what active moves, and that off leaves nothing of Basic
az bicep build --file main.bicep --stdout 2>/dev/null | python3 tests/check_serverless.py || { echo "check_serverless.py failed"; exit 1; }
# the insights role reads conversations and nothing more
R=modules/insights-role.bicep
[ "$(grep -c "^  'Microsoft\.CognitiveServices/" $R)" = 3 ] || { echo "insights role must have exactly three data actions"; exit 1; }
for a in "AIServices/agents/read" "OpenAI/deployments/chat/completions/action" "Language/analyze-text/action"; do
  grep -q "^  'Microsoft.CognitiveServices/accounts/$a'$" $R || { echo "insights role lacks $a"; exit 1; }
done
if grep -n "\*\|/write\|/delete" $R | grep -v "^[0-9]*://"; then echo "insights role has a wildcard, write or delete"; exit 1; fi
grep -q "^        actions: \[\]$" $R || { echo "insights role must have empty actions"; exit 1; }
grep -q "assignableScopes: \[resourceGroup().id\]" $R || { echo "insights role is not limited to the resource group"; exit 1; }
grep -q "scope: foundry$" $R || { echo "insights role is not assigned on the Foundry account"; exit 1; }
[ "$(grep -c "Microsoft.Authorization/roleAssignments@" $R)" = 1 ] || { echo "insights module assigns more than one role"; exit 1; }
if grep -n "insightsIdentity" main.bicep | grep -i "role-foundry\|roleDefinitionId"; then echo "another role for the insights identity"; exit 1; fi
grep -q "principalId: insightsIdentity.outputs.principalId" main.bicep || { echo "insights role is not given to the insights identity"; exit 1; }
for out in INSIGHTS_IDENTITY_ID INSIGHTS_IDENTITY_CLIENT_ID; do
  grep -q "^output $out string" main.bicep || { echo "main.bicep lacks output $out"; exit 1; }
done
# the insights job: nothing is scheduled and no real chat is read until someone says so
grep -q "^param insightsSchedule string = 'off'$" main.bicep || { echo "the insights job is not off by default"; exit 1; }
grep -q '"insightsSchedule": { "value": "${INSIGHTS_SCHEDULE=off}" }' main.parameters.json || { echo "INSIGHTS_SCHEDULE does not default to off"; exit 1; }
grep -q "^param insightsNoticeDate string = ''$" main.bicep || { echo "the notice date is not empty by default"; exit 1; }
grep -q '"insightsNoticeDate": { "value": "${INSIGHTS_NOTICE_DATE=}" }' main.parameters.json || { echo "INSIGHTS_NOTICE_DATE does not default to empty"; exit 1; }
grep -q '"insightsImage": { "value": "${INSIGHTS_IMAGE=}" }' main.parameters.json || { echo "a provision would replace the deployed insights image"; exit 1; }
grep -q "^param schedule string = 'off'$" modules/insights.bicep && grep -q "^param noticeDate string = ''$" modules/insights.bicep || { echo "insights.bicep has other defaults"; exit 1; }
grep -q "name: 'LOG_ANALYTICS_WORKSPACE_ID', value: logAnalyticsWorkspaceId" modules/api-app.bicep || { echo "api-app lacks LOG_ANALYTICS_WORKSPACE_ID"; exit 1; }
grep -q "'__WORKSPACE_ID__', workspaceId" modules/dashboard-workbook.bicep || { echo "the workbook does not get the workspace"; exit 1; }
# what is compiled: the roles and their condition, the identity, the table and the rule, and that no module
# connects Foundry to Application Insights: that would start its server-side tracing of chat text
az bicep build --file main.bicep --stdout 2>/dev/null | python3 tests/check_insights.py || { echo "check_insights.py failed"; exit 1; }
# the dealer: at most one replica, may scale to zero, and its table is the only storage it may touch
grep -q "minReplicas: 0" modules/dealer-app.bicep && grep -q "maxReplicas: 1" modules/dealer-app.bicep || { echo "the dealer must run as 0 to 1 replicas"; exit 1; }
for env in TABLE_ENDPOINT TABLE_NAME SITE_CLIENT_ID; do
  grep -q "name: '$env'" modules/dealer-app.bicep || { echo "dealer-app lacks $env"; exit 1; }
done
grep -q "^var dealerTableName = 'dealer'$" main.bicep && grep -q "name: dealerTableName" modules/storage.bicep && grep -q "tableServices/tables@" modules/storage.bicep || { echo "storage lacks the dealer table"; exit 1; }
grep -q "^param siteClientId string" modules/dealer-app.bicep || { echo "dealer-app lacks siteClientId"; exit 1; }
grep -q '"dealerSiteClientId": { "value": "${DEALER_SITE_CLIENT_ID=}" }' main.parameters.json || { echo "main.parameters.json lacks DEALER_SITE_CLIENT_ID"; exit 1; }
grep -q "scope: table$" modules/role-storage-table.bicep || { echo "the table role is not scoped to a table"; exit 1; }
[ "$(grep -c "Microsoft.Authorization/roleAssignments@" modules/role-storage-table.bicep)" = 1 ] || { echo "the table module assigns more than one role"; exit 1; }
grep -q "^var storageTableDataContributor = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'$" main.bicep || { echo "main.bicep lacks the Table Data Contributor GUID"; exit 1; }
grep -A8 "module dealerReadsTable" main.bicep | grep -q "roleDefinitionId: storageTableDataContributor" || { echo "the dealer does not get Table Data Contributor"; exit 1; }
grep -A8 "module dealerReadsTable" main.bicep | grep -q "principalId: dealerApp.outputs.principalId" || { echo "the table role is not given to the dealer"; exit 1; }
# what is compiled: exactly one role names the dealer, and its scope is the table
az bicep build --file main.bicep --stdout 2>/dev/null | python3 tests/check_dealer.py || { echo "check_dealer.py failed"; exit 1; }
echo "bicep ok"
