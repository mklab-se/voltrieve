// Assigns one built-in role on a single table, so a principal can use that table and no other.
// Pass the role as a GUID, never a name.
param storageAccountName string
param tableName string
param principalId string

@description('Built-in role definition GUID, never the role name.')
param roleDefinitionId string

@allowed(['ServicePrincipal', 'User'])
param principalType string

resource account 'Microsoft.Storage/storageAccounts@2026-09-01' existing = {
  name: storageAccountName
}

resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2026-09-01' existing = {
  parent: account
  name: 'default'
}

resource table 'Microsoft.Storage/storageAccounts/tableServices/tables@2026-09-01' existing = {
  parent: tableService
  name: tableName
}

resource assignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: table
  name: guid(table.id, principalId, roleDefinitionId)
  properties: {
    principalId: principalId
    principalType: principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionId)
  }
}
