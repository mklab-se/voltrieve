// The Voltrieve operations workbook: users, speed, tools, knowledge bases, cost and health,
// read from the chat.turn spans in Application Insights. Point appInsightsId at any Application
// Insights resource whose turns carry the same attributes to reuse it in another solution.
param name string
param location string
param appInsightsId string
@description('Resource id of the Log Analytics workspace with the usage insights.')
param workspaceId string
param tags object = {}

resource workbook 'Microsoft.Insights/workbooks@2023-06-01' = {
  name: guid(resourceGroup().id, name, 'operations-workbook')
  location: location
  tags: tags
  kind: 'shared'
  properties: {
    displayName: 'Voltrieve operations'
    category: 'workbook'
    sourceId: appInsightsId
    serializedData: replace(replace(loadTextContent('../workbooks/operations.json'), '__APPINSIGHTS_ID__', appInsightsId), '__WORKSPACE_ID__', workspaceId)
  }
}

output id string = workbook.id
