// A Serverless search service: no replicas or partitions, billed per compute unit and stored GB.
// A service cannot move between Basic and Serverless, so this one stands next to the Basic one.
param name string
@description('Sweden Central has Serverless. France Central, where the Basic service is, has not.')
param location string
param tags object = {}

resource search 'Microsoft.Search/searchServices@2026-11-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'serverless' }
  identity: { type: 'SystemAssigned' }
  properties: {
    hostingMode: 'Default'
    semanticSearch: 'standard'
    knowledgeRetrieval: 'standard'
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

output name string = search.name
output endpoint string = 'https://${search.name}.search.windows.net'
output principalId string = search.identity.principalId
