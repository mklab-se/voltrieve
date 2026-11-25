param name string
param location string
param tags object = {}
param environmentId string
param registryLoginServer string
@description('Image to run. The placeholder listens on 8000 via ASPNETCORE_HTTP_PORTS; azd deploy swaps it.')
param image string
param pullIdentityId string
param tenantId string
param dealerClientId string
param siteClientId string
param tableEndpoint string
param tableName string
param appInsightsConnectionString string
@description('A host name of our own (DEALER_HOST). Its DNS records must exist: the app refuses a name that it cannot verify. Empty: only the address of the container app.')
param host string = ''
@description('The MCP door: https and the host, as callers see it. Without a client id there is no door.')
param mcpUrl string = ''
param mcpClientId string = ''
@description('The harnesses that may call: "client id=Name;client id=Name".')
param mcpApps string = ''
param mcpAllowCli bool = false
@description('True when the Application ID URI of the door is its address.')
param mcpIdentifierIsUrl bool = false
param mcpIssuer string = ''
@description('Whose dealer is whose: "object id=City;object id=City". Anyone else is refused.')
param doorCities string = ''

// The system-assigned identity is for service auth; the user-assigned one only pulls the image.
// The caller must make this module depend on the pull identity's AcrPull assignment.
resource dealer 'Microsoft.App/containerApps@2026-07-01' = {
  name: name
  location: location
  tags: union(tags, { 'azd-service-name': 'dealer' })
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
          name: 'dealer'
          image: image
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'TENANT_ID', value: tenantId }
            { name: 'DEALER_CLIENT_ID', value: dealerClientId }
            { name: 'SITE_CLIENT_ID', value: siteClientId }
            { name: 'TABLE_ENDPOINT', value: tableEndpoint }
            { name: 'TABLE_NAME', value: tableName }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
            { name: 'OTEL_SERVICE_NAME', value: 'voltrieve-dealer' }
            { name: 'LOG_LEVEL', value: 'INFO' }
            { name: 'MCP_DEALER_URL', value: mcpUrl }
            { name: 'MCP_DEALER_CLIENT_ID', value: mcpClientId }
            { name: 'MCP_APPS', value: mcpApps }
            // Never empty: the service does not start with an empty one.
            { name: 'MCP_ALLOW_CLI', value: mcpAllowCli ? 'true' : 'false' }
            { name: 'MCP_IDENTIFIER_IS_URL', value: mcpIdentifierIsUrl ? 'true' : 'false' }
            { name: 'MCP_ISSUER', value: mcpIssuer }
            { name: 'DEALER_DOOR_CITIES', value: doorCities }
            { name: 'ASPNETCORE_HTTP_PORTS', value: '8000' } // only used by the placeholder image
          ]
        }
      ]
      scale: {
        // One copy, so every open screen gets every event. It may still scale to zero.
        minReplicas: 0
        maxReplicas: 1
      }
    }
  }
}

output name string = dealer.name
output url string = 'https://${dealer.properties.configuration.ingress.fqdn}'
output principalId string = dealer.identity.principalId
