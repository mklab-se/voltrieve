param name string
@description('West Europe rejected new Static Web Apps; East US 2 holds only metadata, content is served globally.')
param location string
param tags object = {}
@description('The azd service this site hosts: web, car or dashboard. It becomes the azd-service-name tag.')
param serviceName string

// Free tier: no idle cost. azd deploys a built SPA to the site tagged with its service name.
resource site 'Microsoft.Web/staticSites@2025-03-01' = {
  name: name
  location: location
  tags: union(tags, { 'azd-service-name': serviceName })
  sku: {
    name: 'Free'
    tier: 'Free'
  }
  properties: {}
}

output name string = site.name
output url string = 'https://${site.properties.defaultHostname}'
