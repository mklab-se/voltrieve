// A custom role that can read conversations and call a model, and nothing else: no actions,
// three data actions, no write and no delete. Assigned on the Foundry account only.
param foundryName string
param principalId string

@description('Makes the role name unique, so two environments in one subscription do not collide.')
param nameSuffix string

var dataActions = [
  'Microsoft.CognitiveServices/accounts/AIServices/agents/read'
  'Microsoft.CognitiveServices/accounts/OpenAI/deployments/chat/completions/action'
  'Microsoft.CognitiveServices/accounts/Language/analyze-text/action'
]

resource foundry 'Microsoft.CognitiveServices/accounts@2026-11-15-preview' existing = {
  name: foundryName
}

resource role 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'voltrieve-insights-reader')
  properties: {
    roleName: 'Voltrieve insights reader ${nameSuffix}'
    description: 'Reads conversations and calls a model. Cannot create, change or delete anything.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [
      {
        actions: []
        notActions: []
        dataActions: dataActions
        notDataActions: []
      }
    ]
  }
}

resource assignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundry
  name: guid(foundry.id, principalId, role.id)
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: role.id
  }
}

output roleDefinitionId string = role.id
