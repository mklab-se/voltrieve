param name string
param location string
param tags object = {}
param environmentId string
param registryLoginServer string
@description('Image to run. The placeholder listens on 8000 via ASPNETCORE_HTTP_PORTS; azd deploy swaps it.')
param image string
param pullIdentityId string
param tenantId string
param apiClientId string
param carClientId string = ''
param webClientId string = ''
param cliClientId string = ''
param dealerUrl string
param dealerClientId string
param foundryProjectEndpoint string
param chatDeployment string
param searchEndpoint string
param promptAgentCli string
param promptAgentWeb string
param promptAgentCar string
param hostedAgentName string
@description('Comma separated list of allowed browser origins.')
param corsOrigins string
param appInsightsConnectionString string
@description('Id (customerId) of the Log Analytics workspace with the usage insights.')
param logAnalyticsWorkspaceId string
@description('Storage account whose documents users may open from source links.')
param storageAccountName string
@description('Comma separated containers whose documents may be linked. Others are never signed.')
param sourceContainers string
@description('A host name of our own (API_HOST). Its DNS records must exist: the app refuses a name that it cannot verify. Empty: only the address of the container app.')
param host string = ''
@description('The MCP doors: https and the host, as callers see it. A door without a client id does not exist.')
param mcpUrl string = ''
param mcpOperationsClientId string = ''
param mcpCarClientId string = ''
param mcpCustomerClientId string = ''
param mcpRunaClientId string = ''
@description('The harnesses that may call: "client id=Name;client id=Name".')
param mcpApps string = ''
param mcpAllowCli bool = false
@description('True when the Application ID URI of the doors is their address.')
param mcpIdentifierIsUrl bool = false
param mcpIssuer string = ''

// The system-assigned identity calls Foundry, Search and the dealer; the user-assigned one only
// pulls the image. AZURE_CLIENT_ID is deliberately not set, so DefaultAzureCredential picks the
// system identity. The caller must make this module depend on the pull identity's AcrPull assignment.
// Ingress does not buffer responses, so server-sent events stream through; Container Apps closes
// idle connections after 240 seconds (not configurable), so the API sends SSE keep-alives.
resource api 'Microsoft.App/containerApps@2026-07-01' = {
  name: name
  location: location
  tags: union(tags, { 'azd-service-name': 'api' })
  identity: {
    type: 'SystemAssigned,UserAssigned'
    userAssignedIdentities: { '${pullIdentityId}': {} }
  }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
        // Auto: without a certificate the name answers on http only, and the managed certificate is bound once it
        // exists (host-certificate.bicep). The same value on every provision, so none of them drops the name.
        customDomains: empty(host) ? null : [
          {
            name: host
            bindingType: 'Auto'
          }
        ]
      }
      registries: [
        {
          server: registryLoginServer
          identity: pullIdentityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: image
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'TENANT_ID', value: tenantId }
            { name: 'API_CLIENT_ID', value: apiClientId }
            { name: 'CAR_CLIENT_ID', value: carClientId }
            { name: 'WEB_CLIENT_ID', value: webClientId }
            { name: 'CLI_CLIENT_ID', value: cliClientId }
            { name: 'DEALER_URL', value: dealerUrl }
            { name: 'DEALER_CLIENT_ID', value: dealerClientId }
            { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
            { name: 'CHAT_DEPLOYMENT', value: chatDeployment }
            { name: 'SEARCH_ENDPOINT', value: searchEndpoint }
            { name: 'STORAGE_ACCOUNT_NAME', value: storageAccountName }
            { name: 'SOURCE_CONTAINERS', value: sourceContainers }
            { name: 'PROMPT_AGENT_CLI', value: promptAgentCli }
            { name: 'PROMPT_AGENT_WEB', value: promptAgentWeb }
            { name: 'PROMPT_AGENT_CAR', value: promptAgentCar }
            { name: 'HOSTED_AGENT_NAME', value: hostedAgentName }
            { name: 'CORS_ORIGINS', value: corsOrigins }
            { name: 'AGENT_BACKEND', value: 'loop' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
            { name: 'LOG_ANALYTICS_WORKSPACE_ID', value: logAnalyticsWorkspaceId }
            { name: 'OTEL_SERVICE_NAME', value: 'voltrieve-api' }
            { name: 'AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING', value: 'true' }
            // Our own spans never carry message text, whatever a library defaults to.
            { name: 'OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT', value: 'false' }
            { name: 'LOG_LEVEL', value: 'INFO' }
            { name: 'MCP_API_URL', value: mcpUrl }
            { name: 'MCP_OPERATIONS_CLIENT_ID', value: mcpOperationsClientId }
            { name: 'MCP_CAR_CLIENT_ID', value: mcpCarClientId }
            { name: 'MCP_CUSTOMER_CLIENT_ID', value: mcpCustomerClientId }
            { name: 'MCP_RUNA_CLIENT_ID', value: mcpRunaClientId }
            { name: 'MCP_APPS', value: mcpApps }
            // Never empty: the service does not start with an empty one.
            { name: 'MCP_ALLOW_CLI', value: mcpAllowCli ? 'true' : 'false' }
            { name: 'MCP_IDENTIFIER_IS_URL', value: mcpIdentifierIsUrl ? 'true' : 'false' }
            { name: 'MCP_ISSUER', value: mcpIssuer }
            { name: 'ASPNETCORE_HTTP_PORTS', value: '8000' } // only used by the placeholder image
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
      }
    }
  }
}

output name string = api.name
output url string = 'https://${api.properties.configuration.ingress.fqdn}'
output principalId string = api.identity.principalId
@description('For the TXT record asuid.<host> in DNS. Public there.')
output hostVerificationId string = api.properties.customDomainVerificationId
