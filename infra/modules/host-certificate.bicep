// The free certificate of a host name of our own. The app must have the name before it can be made, and the CNAME
// of the name must point at the app, for as long as the certificate is renewed. A wait of minutes the first time.
param environmentName string
param location string
param tags object = {}
param host string

resource environment 'Microsoft.App/managedEnvironments@2026-07-01' existing = {
  name: environmentName
}

// The name follows the host, since the host of a certificate cannot be changed.
resource certificate 'Microsoft.App/managedEnvironments/managedCertificates@2026-07-01' = {
  parent: environment
  name: 'mc-${uniqueString(host)}'
  location: location
  tags: tags
  properties: {
    subjectName: host
    domainControlValidation: 'CNAME'
  }
}
