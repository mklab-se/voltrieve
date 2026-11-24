param name string
param location string
param tags object = {}
param docsContainer string
param partsContainer string
param dealerTableName string

resource storage 'Microsoft.Storage/storageAccounts@2026-09-01' = {
  name: name
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2026-09-01' = {
  parent: storage
  name: 'default'
}

resource docs 'Microsoft.Storage/storageAccounts/blobServices/containers@2026-09-01' = {
  parent: blobService
  name: docsContainer
  properties: {
    publicAccess: 'None'
  }
}

resource parts 'Microsoft.Storage/storageAccounts/blobServices/containers@2026-09-01' = {
  parent: blobService
  name: partsContainer
  properties: {
    publicAccess: 'None'
  }
}

resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2026-09-01' = {
  parent: storage
  name: 'default'
}

resource dealerTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2026-09-01' = {
  parent: tableService
  name: dealerTableName
}

output name string = storage.name
output id string = storage.id
output docsContainer string = docs.name
output partsContainer string = parts.name
output dealerTable string = dealerTable.name
output tableEndpoint string = 'https://${storage.name}.table.${environment().suffixes.storage}'
