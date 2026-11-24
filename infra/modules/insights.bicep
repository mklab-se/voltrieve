// The usage insights: the table with the totals, the rule that takes them in, the nightly job and its
// roles. The job may save to this rule and read this table, next to AcrPull (app-identity.bicep) and
// reading conversations (insights-role.bicep), and nothing else. The API may read this table only.
param jobName string
param ruleName string
param location string
param tags object = {}
param workspaceName string
param environmentId string
param registryLoginServer string
@description('The deployed image (INSIGHTS_IMAGE, set by scripts/deploy-insights.sh), so provision keeps it. Empty before the first: a public sample that prints a line and ends.')
param image string = ''
param identityId string
param identityClientId string
param identityPrincipalId string
param apiPrincipalId string
param foundryProjectEndpoint string
param foundryOpenAiEndpoint string
param languageEndpoint string
param modelDeployment string
@description('off: the job starts by hand only. on: every night at 02:30 UTC, once there is an image.')
@allowed(['off', 'on'])
param schedule string = 'off'
@description('The first full UTC day the notice was live, as YYYY-MM-DD. Real conversations started before it are never read. Empty: none is read.')
param noticeDate string = ''

var placeholderImage = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'
// The public sample is never on a schedule: the identity it would run with reads everyone's chats.
var byHand = schedule == 'off' || empty(image)
var tableName = 'VoltrieveInsights_CL'
var streamName = 'Custom-${tableName}'
var monitoringMetricsPublisher = '3913510d-42f4-4e42-8a64-420c390055eb'
var logAnalyticsDataReader = '3b03c2da-16b3-4a49-8834-0f8130efdd3b'

// The same as COLUMNS in services/insights/src/voltrieve_insights/store.py (a test compares them).
var columns = [
  { name: 'TimeGenerated', type: 'datetime' }
  { name: 'Run', type: 'datetime' }
  { name: 'WindowStart', type: 'datetime' }
  { name: 'WindowEnd', type: 'datetime' }
  { name: 'Source', type: 'string' }
  { name: 'Cut', type: 'string' }
  { name: 'Client', type: 'string' }
  { name: 'Topic', type: 'string' }
  { name: 'CarModel', type: 'string' }
  { name: 'RequestKind', type: 'string' }
  { name: 'Result', type: 'string' }
  { name: 'GroupName', type: 'string' }
  { name: 'Conversations', type: 'int' }
  { name: 'People', type: 'int' }
  { name: 'SampleShare', type: 'real' }
]

resource workspace 'Microsoft.OperationalInsights/workspaces@2026-03-01' existing = {
  name: workspaceName
}

// Twelve months for the totals. The workspace itself stays at 30 days.
resource table 'Microsoft.OperationalInsights/workspaces/tables@2025-07-01' = {
  parent: workspace
  name: tableName
  properties: {
    plan: 'Analytics'
    retentionInDays: 365
    totalRetentionInDays: 365
    schema: {
      name: tableName
      // A table spells the type dateTime, and a stream datetime.
      columns: [for column in columns: {
        name: column.name
        type: column.type == 'datetime' ? 'dateTime' : column.type
      }]
    }
  }
}

// Direct: the rule has its own address to send to, so no data collection endpoint is needed.
resource rule 'Microsoft.Insights/dataCollectionRules@2023-03-11' = {
  name: ruleName
  location: location
  tags: tags
  kind: 'Direct'
  properties: {
    streamDeclarations: {
      '${streamName}': {
        columns: columns
      }
    }
    destinations: {
      logAnalytics: [
        {
          name: 'insights'
          workspaceResourceId: workspace.id
        }
      ]
    }
    dataFlows: [
      {
        streams: [streamName]
        destinations: ['insights']
        transformKql: 'source'
        outputStream: streamName
      }
    ]
  }
  dependsOn: [table]
}

resource saves 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: rule
  name: guid(rule.id, identityPrincipalId, monitoringMetricsPublisher)
  properties: {
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringMetricsPublisher)
  }
}

// The job reads its earlier group names, and the API what the dashboard shows. The condition leaves
// them this table only: without it, the role would open every log in the workspace.
var onlyThisTable = '((!(ActionMatches{\'Microsoft.OperationalInsights/workspaces/tables/data/read\'})) OR (@Resource[Microsoft.OperationalInsights/workspaces/tables:name] StringEquals \'${tableName}\'))'

resource reads 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for reader in [identityPrincipalId, apiPrincipalId]: {
  scope: workspace
  name: guid(workspace.id, reader, logAnalyticsDataReader)
  properties: {
    principalId: reader
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', logAnalyticsDataReader)
    condition: onlyThisTable
    conditionVersion: '2.0'
  }
}]

resource job 'Microsoft.App/jobs@2026-07-01' = {
  name: jobName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  properties: {
    environmentId: environmentId
    configuration: {
      triggerType: byHand ? 'Manual' : 'Schedule'
      manualTriggerConfig: byHand ? { parallelism: 1, replicaCompletionCount: 1 } : null
      scheduleTriggerConfig: byHand ? null : {
        cronExpression: '30 2 * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      replicaTimeout: 1800 // the run ends itself after 25 minutes
      replicaRetryLimit: 0 // a failed night saves nothing and is not tried again
      registries: [
        {
          server: registryLoginServer
          identity: identityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'insights'
          image: empty(image) ? placeholderImage : image
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
            { name: 'FOUNDRY_OPENAI_ENDPOINT', value: foundryOpenAiEndpoint }
            { name: 'LANGUAGE_ENDPOINT', value: languageEndpoint }
            { name: 'INSIGHTS_INGESTION_ENDPOINT', value: rule.properties.endpoints.logsIngestion }
            { name: 'INSIGHTS_RULE_ID', value: rule.properties.immutableId }
            { name: 'LOG_ANALYTICS_WORKSPACE_ID', value: workspace.properties.customerId }
            { name: 'AZURE_CLIENT_ID', value: identityClientId }
            { name: 'INSIGHTS_NOTICE_DATE', value: noticeDate }
            { name: 'INSIGHTS_LIMIT', value: '5' }
            { name: 'INSIGHTS_CAP', value: '2000' }
            { name: 'MODEL_DEPLOYMENT', value: modelDeployment }
          ]
        }
      ]
    }
  }
}

output jobName string = job.name
