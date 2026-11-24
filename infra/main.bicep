targetScope = 'subscription'

@minLength(1)
@maxLength(22)
@description('Name of the azd environment, used in resource names and tags.')
param environmentName string

@description('Primary Azure region.')
param location string = 'swedencentral'

@description('Region for Azure AI Search. Sweden Central and West Europe cannot create new search services.')
param searchLocation string = 'francecentral'

@description('A Serverless search service next to the Basic one (SEARCH_SERVERLESS). off: not deployed. standby: deployed, nothing uses it. active: SEARCH_ENDPOINT points at it. A deployed service is not deleted by going back to off.')
@allowed(['off', 'standby', 'active'])
param serverlessSearch string = 'off'

@description('The Basic search service (SEARCH_BASIC). on: deployed. off: not deployed, nothing refers to it, and only allowed with serverlessSearch active. A deployed service is not deleted by going to off: docs/deploy.md says how.')
@allowed(['on', 'off'])
param basicSearch string = 'on'

@description('Region for the Serverless search service.')
param serverlessSearchLocation string = 'swedencentral'

@description('Object id of the deployer (AZURE_PRINCIPAL_ID), granted data-plane roles.')
param principalId string

// Empty defaults because azd checks required parameters before the preprovision
// hook that produces these values has run. The hook fails hard, so a real
// deployment always has them.
param tenantId string = ''
param dealerClientId string = ''
@description('The app registration of the dealer page. The page reads it from /config.json.')
param dealerSiteClientId string = ''
param apiClientId string = ''
@description('The app registrations of the car, the website and the command line, to tell their tokens apart in the usage count. Empty counts a client as other.')
param carClientId string = ''
param webClientId string = ''
param cliClientId string = ''
@description('True when the API Container App already exists (azd sets SERVICE_API_RESOURCE_EXISTS), so provision keeps its deployed image.')
param apiExists bool = false
@description('West Europe rejected new Static Web Apps; East US 2 holds only metadata, content is served globally.')
param webLocation string = 'eastus2'
@description('True when the dealer Container App already exists (azd sets SERVICE_DEALER_RESOURCE_EXISTS), so provision keeps its deployed image.')
param dealerExists bool = false
@description('Principal id of the hosted agent identity (HOSTED_AGENT_PRINCIPAL_ID, set by agents/hosted/deploy.py). Empty until the hosted agent exists.')
param hostedAgentPrincipalId string = ''
param hostedAgentName string = 'voltrieve-hosted'
@description('The nightly usage insights (INSIGHTS_SCHEDULE). off: the job starts by hand only. on: every night at 02:30 UTC, once INSIGHTS_IMAGE is set.')
@allowed(['off', 'on'])
param insightsSchedule string = 'off'
@description('The first full UTC day the notice about the insights was live, as YYYY-MM-DD (INSIGHTS_NOTICE_DATE). Earlier conversations are never read. Empty: no real conversation is read.')
param insightsNoticeDate string = ''
@description('The deployed image of the insights job (INSIGHTS_IMAGE, set by scripts/deploy-insights.sh). Empty until it has been deployed.')
param insightsImage string = ''
param chatModelName string = 'gpt-6.1-terra'
param chatModelVersion string = '2026-10-26'
@description('The model Search plans queries with (PLANNING_MODEL_NAME). gpt-6-luna is opt-in: a GPT-6 model needs every caller of Search to use 2026-11-01-preview or later, so deploy the code before choosing it.')
@allowed(['gpt-5.4-mini', 'gpt-6-luna'])
param planningModelName string = 'gpt-5.4-mini'
param embeddingModelName string = 'text-embedding-3-large'
param embeddingModelVersion string = '1'

// Both are deployed, so changing the planner is planningModelName alone, in both directions.
var planningModels = [
  { name: 'gpt-5.4-mini', version: '2026-03-17' }
  { name: 'gpt-6-luna', version: '2026-09-22' }
]
var deployServerless = serverlessSearch != 'off'
var serverlessActive = serverlessSearch == 'active'
// The failure is part of every condition below, so it stops the deployment before it creates anything.
var deployBasic = basicSearch == 'on'
  ? true
  : serverlessActive ? false : fail('SEARCH_BASIC is off, which needs SEARCH_SERVERLESS active, because nothing else would answer searches')
var searchEndpoint = serverlessActive ? searchServerless!.outputs.endpoint : search!.outputs.endpoint
var placeholderImage = 'mcr.microsoft.com/dotnet/samples:aspnetapp'
var dealerName = 'ca-dealer-${environmentName}'
var apiName = 'ca-api-${environmentName}'
var docsContainer = 'voltrieve-docs'
var partsContainer = 'voltrieve-parts'
var token = toLower(uniqueString(subscription().id, environmentName, location))
var tags = { 'azd-env-name': environmentName, app: 'voltrieve' }

var storageBlobDataReader = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var storageTableDataContributor = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
var dealerTableName = 'dealer'
var cognitiveServicesOpenAIUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var cognitiveServicesUser = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var foundryProjectManager = 'eadc314b-1a2d-4efa-be10-5d325db5065e'
var acrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var containerRegistryRepositoryReader = 'b93aa761-3e63-49ed-ac28-beffa264f7ac'
var searchIndexDataReader = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
var searchServiceContributor = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
var searchIndexDataContributor = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
var storageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var foundryUser = '53ca6127-db72-4b80-b1b0-d745d6d5456d'
var storageBlobDelegator = 'db58b8e5-c6ad-4a2a-8342-4190687cbf4a'

resource rg 'Microsoft.Resources/resourceGroups@2026-06-01' = {
  name: 'rg-voltrieve-${environmentName}'
  location: location
  tags: tags
}

module monitoring 'modules/monitoring.bicep' = {
  scope: rg
  params: {
    name: 'voltrieve-${token}'
    location: location
    tags: tags
  }
}

module dashboardWorkbook 'modules/dashboard-workbook.bicep' = {
  scope: rg
  params: {
    name: 'voltrieve-${token}'
    location: location
    appInsightsId: monitoring.outputs.appInsightsId
    workspaceId: monitoring.outputs.logAnalyticsId
    tags: tags
  }
}

module foundry 'modules/foundry.bicep' = {
  scope: rg
  params: {
    name: 'ai-voltrieve-${token}'
    location: location
    tags: tags
    projectName: 'voltrieve'
    chatModelName: chatModelName
    chatModelVersion: chatModelVersion
    planningModels: planningModels
    embeddingModelName: embeddingModelName
    embeddingModelVersion: embeddingModelVersion
  }
}

module search 'modules/search.bicep' = if (deployBasic) {
  scope: rg
  params: {
    name: 'srch-voltrieve-${token}'
    location: searchLocation
    tags: tags
  }
}

module searchServerless 'modules/search-serverless.bicep' = if (deployServerless) {
  name: 'search-serverless'
  scope: rg
  params: {
    name: 'srch-voltrieve-sl-${token}'
    location: serverlessSearchLocation
    tags: tags
  }
}

module searchServerlessRoles 'modules/search-serverless-roles.bicep' = if (deployServerless) {
  name: 'search-serverless-roles'
  scope: rg
  params: {
    searchName: searchServerless!.outputs.name
    searchPrincipalId: searchServerless!.outputs.principalId
    storageName: storage.outputs.name
    foundryName: foundry.outputs.name
    readerPrincipalIds: concat(
      [apiApp.outputs.principalId, foundry.outputs.projectPrincipalId],
      empty(hostedAgentPrincipalId) ? [] : [hostedAgentPrincipalId]
    )
    deployerPrincipalId: principalId
  }
}

module storage 'modules/storage.bicep' = {
  scope: rg
  params: {
    name: 'stvoltrieve${token}'
    location: location
    tags: tags
    docsContainer: docsContainer
    partsContainer: partsContainer
    dealerTableName: dealerTableName
  }
}

module registry 'modules/registry.bicep' = {
  scope: rg
  params: {
    name: 'crvoltrieve${token}'
    location: location
    tags: tags
  }
}

module containerAppsEnv 'modules/containerapps-env.bicep' = {
  scope: rg
  params: {
    name: 'cae-voltrieve-${environmentName}'
    location: location
    tags: tags
    logAnalyticsName: monitoring.outputs.logAnalyticsName
  }
}

module dealerIdentity 'modules/app-identity.bicep' = {
  scope: rg
  params: {
    name: 'id-dealer-${environmentName}'
    location: location
    tags: tags
    registryName: registry.outputs.name
  }
}

module dealerCurrent 'modules/fetch-container-image.bicep' = if (dealerExists) {
  scope: rg
  params: {
    name: dealerName
  }
}

// dealerIdentity includes the AcrPull assignment, so depending on it orders the app after the grant.
module dealerApp 'modules/dealer-app.bicep' = {
  scope: rg
  params: {
    name: dealerName
    location: location
    tags: tags
    environmentId: containerAppsEnv.outputs.id
    registryLoginServer: registry.outputs.loginServer
    image: dealerExists ? dealerCurrent.?outputs.image ?? placeholderImage : placeholderImage
    pullIdentityId: dealerIdentity.outputs.id
    tenantId: tenantId
    dealerClientId: dealerClientId
    siteClientId: dealerSiteClientId
    tableEndpoint: storage.outputs.tableEndpoint
    tableName: storage.outputs.dealerTable
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
  }
}

// The dealer may read and write its own table and nothing else in the account.
module dealerUsesTable 'modules/role-storage-table.bicep' = {
  scope: rg
  params: {
    storageAccountName: storage.outputs.name
    tableName: storage.outputs.dealerTable
    principalId: dealerApp.outputs.principalId
    roleDefinitionId: storageTableDataContributor
    principalType: 'ServicePrincipal'
  }
}

module apiIdentity 'modules/app-identity.bicep' = {
  scope: rg
  params: {
    name: 'id-api-${environmentName}'
    location: location
    tags: tags
    registryName: registry.outputs.name
  }
}

// The usage insights job reads conversations with this identity (AcrPull comes with the module).
module insightsIdentity 'modules/app-identity.bicep' = {
  scope: rg
  params: {
    name: 'id-insights-${environmentName}'
    location: location
    tags: tags
    registryName: registry.outputs.name
  }
}

module insightsRole 'modules/insights-role.bicep' = {
  scope: rg
  params: {
    foundryName: foundry.outputs.name
    principalId: insightsIdentity.outputs.principalId
    nameSuffix: token
  }
}

// insightsIdentity includes the AcrPull assignment, so depending on it orders the job after the grant.
module insights 'modules/insights.bicep' = {
  scope: rg
  params: {
    jobName: 'caj-insights-${environmentName}'
    ruleName: 'dcr-insights-${token}'
    location: location
    tags: tags
    workspaceName: monitoring.outputs.logAnalyticsName
    environmentId: containerAppsEnv.outputs.id
    registryLoginServer: registry.outputs.loginServer
    image: insightsImage
    identityId: insightsIdentity.outputs.id
    identityClientId: insightsIdentity.outputs.clientId
    identityPrincipalId: insightsIdentity.outputs.principalId
    apiPrincipalId: apiApp.outputs.principalId
    foundryProjectEndpoint: foundry.outputs.projectEndpoint
    foundryOpenAiEndpoint: foundry.outputs.openAiEndpoint
    languageEndpoint: 'https://${foundry.outputs.name}.cognitiveservices.azure.com'
    modelDeployment: 'gpt-6-luna' // always deployed, see planningModels
    schedule: insightsSchedule
    noticeDate: insightsNoticeDate
  }
}

module apiCurrent 'modules/fetch-container-image.bicep' = if (apiExists) {
  scope: rg
  params: {
    name: apiName
  }
}

module web 'modules/static-web-app.bicep' = {
  scope: rg
  params: {
    name: 'stapp-voltrieve-${environmentName}'
    location: webLocation
    tags: tags
    serviceName: 'web'
  }
}

module car 'modules/static-web-app.bicep' = {
  scope: rg
  params: {
    name: 'stapp-voltrieve-car-${environmentName}'
    location: webLocation
    tags: tags
    serviceName: 'car'
  }
}

module dashboard 'modules/static-web-app.bicep' = {
  scope: rg
  params: {
    name: 'stapp-voltrieve-dashboard-${environmentName}'
    location: webLocation
    tags: tags
    serviceName: 'dashboard'
  }
}

module apiApp 'modules/api-app.bicep' = {
  scope: rg
  params: {
    name: apiName
    location: location
    tags: tags
    environmentId: containerAppsEnv.outputs.id
    registryLoginServer: registry.outputs.loginServer
    image: apiExists ? apiCurrent.?outputs.image ?? placeholderImage : placeholderImage
    pullIdentityId: apiIdentity.outputs.id
    tenantId: tenantId
    apiClientId: apiClientId
    carClientId: carClientId
    webClientId: webClientId
    cliClientId: cliClientId
    dealerUrl: dealerApp.outputs.url
    dealerClientId: dealerClientId
    foundryProjectEndpoint: foundry.outputs.projectEndpoint
    chatDeployment: foundry.outputs.chatDeployment
    searchEndpoint: searchEndpoint
    promptAgentCli: 'voltrieve-prompt-cli'
    promptAgentWeb: 'voltrieve-prompt-web'
    promptAgentCar: 'voltrieve-prompt-car'
    hostedAgentName: hostedAgentName
    corsOrigins: '${web.outputs.url},${car.outputs.url},${dashboard.outputs.url},http://localhost:5173,http://localhost:5174,http://localhost:5175'
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsCustomerId
    storageAccountName: storage.outputs.name
    // Only the documents container: the parts catalogs are internal and never linked.
    sourceContainers: storage.outputs.docsContainer
  }
}

// Source links: the API signs user delegation SAS links to documents users may open. It needs
// the delegation key on the account and read on the documents container, and nothing more.
module apiSignsLinks 'modules/role-storage.bicep' = {
  scope: rg
  params: {
    resourceName: storage.outputs.name
    principalId: apiApp.outputs.principalId
    roleDefinitionId: storageBlobDelegator
    principalType: 'ServicePrincipal'
  }
}

module apiReadsDocuments 'modules/role-storage-container.bicep' = {
  scope: rg
  params: {
    storageAccountName: storage.outputs.name
    containerName: storage.outputs.docsContainer
    principalId: apiApp.outputs.principalId
    roleDefinitionId: storageBlobDataReader
    principalType: 'ServicePrincipal'
  }
}

// The API calls Foundry (agents and chat) and reads the search index with its own identity.
module apiUsesFoundry 'modules/role-foundry.bicep' = {
  scope: rg
  params: {
    resourceName: foundry.outputs.name
    principalId: apiApp.outputs.principalId
    roleDefinitionId: foundryUser
    principalType: 'ServicePrincipal'
  }
}

module apiReadsSearch 'modules/role-search.bicep' = if (deployBasic) {
  scope: rg
  params: {
    resourceName: search!.outputs.name
    principalId: apiApp.outputs.principalId
    roleDefinitionId: searchIndexDataReader
    principalType: 'ServicePrincipal'
  }
}

// The hosted agent has its own identity and the same data-plane access as the API: it calls
// the model and reads the index itself. Dealer.Access comes from scripts/deploy-agents.sh.
module hostedUsesFoundry 'modules/role-foundry.bicep' = if (!empty(hostedAgentPrincipalId)) {
  scope: rg
  params: {
    resourceName: foundry.outputs.name
    principalId: hostedAgentPrincipalId
    roleDefinitionId: foundryUser
    principalType: 'ServicePrincipal'
  }
}

module hostedReadsSearch 'modules/role-search.bicep' = if (deployBasic && !empty(hostedAgentPrincipalId)) {
  scope: rg
  params: {
    resourceName: search!.outputs.name
    principalId: hostedAgentPrincipalId
    roleDefinitionId: searchIndexDataReader
    principalType: 'ServicePrincipal'
  }
}

// Role assignments are declared on the target resource (least privilege).
module searchReadsStorage 'modules/role-storage.bicep' = if (deployBasic) {
  scope: rg
  params: {
    resourceName: storage.outputs.name
    principalId: search!.outputs.principalId
    roleDefinitionId: storageBlobDataReader
    principalType: 'ServicePrincipal'
  }
}

module searchUsesFoundry 'modules/role-foundry.bicep' = if (deployBasic) {
  scope: rg
  params: {
    resourceName: foundry.outputs.name
    principalId: search!.outputs.principalId
    roleDefinitionId: cognitiveServicesOpenAIUser
    principalType: 'ServicePrincipal'
  }
}

// The knowledge base calls the chat model for query planning, which needs this role.
module searchPlansWithFoundry 'modules/role-foundry.bicep' = if (deployBasic) {
  scope: rg
  params: {
    resourceName: foundry.outputs.name
    principalId: search!.outputs.principalId
    roleDefinitionId: cognitiveServicesUser
    principalType: 'ServicePrincipal'
  }
}

// The project identity calls the knowledge base MCP endpoint for prompt agents.
module projectReadsSearch 'modules/role-search.bicep' = if (deployBasic) {
  scope: rg
  params: {
    resourceName: search!.outputs.name
    principalId: foundry.outputs.projectPrincipalId
    roleDefinitionId: searchIndexDataReader
    principalType: 'ServicePrincipal'
  }
}

// The project identity pulls hosted agent images. AcrPull covers a registry in legacy
// permission mode, Repository Reader covers one using repository permissions.
module projectPullsImages 'modules/role-registry.bicep' = {
  scope: rg
  params: {
    resourceName: registry.outputs.name
    principalId: foundry.outputs.projectPrincipalId
    roleDefinitionId: acrPull
    principalType: 'ServicePrincipal'
  }
}

module projectReadsRepositories 'modules/role-registry.bicep' = {
  scope: rg
  params: {
    resourceName: registry.outputs.name
    principalId: foundry.outputs.projectPrincipalId
    roleDefinitionId: containerRegistryRepositoryReader
    principalType: 'ServicePrincipal'
  }
}

module kbConnection 'modules/foundry-kb-connection.bicep' = {
  scope: rg
  params: {
    accountName: foundry.outputs.name
    projectName: foundry.outputs.projectName
    searchEndpoint: searchEndpoint
    knowledgeBaseName: 'voltrieve-kb'
  }
}

module partsKbConnection 'modules/foundry-kb-connection.bicep' = {
  scope: rg
  params: {
    accountName: foundry.outputs.name
    projectName: foundry.outputs.projectName
    searchEndpoint: searchEndpoint
    knowledgeBaseName: 'voltrieve-parts-kb'
    connectionName: 'voltrieve-parts-kb-mcp'
  }
}

// Creating agents and project connections needs this role on the deployer.
module deployerManagesProject 'modules/role-foundry.bicep' = if (!empty(principalId)) {
  scope: rg
  params: {
    resourceName: foundry.outputs.name
    principalId: principalId
    roleDefinitionId: foundryProjectManager
    principalType: 'User'
  }
}

module deployerSearchService 'modules/role-search.bicep' = if (deployBasic && !empty(principalId)) {
  scope: rg
  params: {
    resourceName: search!.outputs.name
    principalId: principalId
    roleDefinitionId: searchServiceContributor
    principalType: 'User'
  }
}

module deployerSearchData 'modules/role-search.bicep' = if (deployBasic && !empty(principalId)) {
  scope: rg
  params: {
    resourceName: search!.outputs.name
    principalId: principalId
    roleDefinitionId: searchIndexDataContributor
    principalType: 'User'
  }
}

module deployerStorage 'modules/role-storage.bicep' = if (!empty(principalId)) {
  scope: rg
  params: {
    resourceName: storage.outputs.name
    principalId: principalId
    roleDefinitionId: storageBlobDataContributor
    principalType: 'User'
  }
}

module deployerFoundry 'modules/role-foundry.bicep' = if (!empty(principalId)) {
  scope: rg
  params: {
    resourceName: foundry.outputs.name
    principalId: principalId
    roleDefinitionId: foundryUser
    principalType: 'User'
  }
}

output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_LOCATION string = location
output DEPLOYER_PRINCIPAL_ID string = principalId
output FOUNDRY_ACCOUNT_NAME string = foundry.outputs.name
output FOUNDRY_ENDPOINT string = foundry.outputs.endpoint
output FOUNDRY_PROJECT_ENDPOINT string = foundry.outputs.projectEndpoint
output FOUNDRY_OPENAI_ENDPOINT string = foundry.outputs.openAiEndpoint
output CHAT_DEPLOYMENT string = foundry.outputs.chatDeployment
output CHAT_MODEL string = chatModelName
output PLANNING_DEPLOYMENT string = planningModelName // a deployment is named after its model
output PLANNING_MODEL string = planningModelName
output FOUNDRY_PROJECT_NAME string = foundry.outputs.projectName
output KB_CONNECTION_NAME string = kbConnection.outputs.name
output PARTS_KB_CONNECTION_NAME string = partsKbConnection.outputs.name
output EMBEDDING_DEPLOYMENT string = foundry.outputs.embeddingDeployment
output EMBEDDING_MODEL string = embeddingModelName
output SEARCH_ENDPOINT string = searchEndpoint
output SEARCH_SERVICE_NAME string = serverlessActive ? searchServerless!.outputs.name : search!.outputs.name
output SEARCH_BASIC_ENDPOINT string = deployBasic ? search!.outputs.endpoint : ''
output SEARCH_SERVERLESS_ENDPOINT string = deployServerless ? searchServerless!.outputs.endpoint : ''
output STORAGE_ACCOUNT_NAME string = storage.outputs.name
output STORAGE_RESOURCE_ID string = storage.outputs.id
output DOCS_CONTAINER string = storage.outputs.docsContainer
output PARTS_CONTAINER string = storage.outputs.partsContainer
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = registry.outputs.loginServer
output AZURE_CONTAINER_REGISTRY_NAME string = registry.outputs.name
output CONTAINER_APPS_ENVIRONMENT_ID string = containerAppsEnv.outputs.id
output APPLICATIONINSIGHTS_CONNECTION_STRING string = monitoring.outputs.appInsightsConnectionString
output APPLICATIONINSIGHTS_ID string = monitoring.outputs.appInsightsId
output DASHBOARD_WORKBOOK_ID string = dashboardWorkbook.outputs.id
output DEALER_URL string = dealerApp.outputs.url
output DEALER_PRINCIPAL_ID string = dealerApp.outputs.principalId
output HOSTED_AGENT_NAME string = hostedAgentName
output API_URL string = apiApp.outputs.url
output API_PRINCIPAL_ID string = apiApp.outputs.principalId
output WEB_URL string = web.outputs.url
output CAR_URL string = car.outputs.url
output DASHBOARD_URL string = dashboard.outputs.url
output INSIGHTS_IDENTITY_ID string = insightsIdentity.outputs.id
output INSIGHTS_IDENTITY_CLIENT_ID string = insightsIdentity.outputs.clientId
output INSIGHTS_JOB_NAME string = insights.outputs.jobName
output LOG_ANALYTICS_WORKSPACE_ID string = monitoring.outputs.logAnalyticsCustomerId
