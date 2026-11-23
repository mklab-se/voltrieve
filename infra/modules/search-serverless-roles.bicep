// Who may use the Serverless search service, and what its identity may use. Separate from the
// service itself, because the API depends on the endpoint and these roles depend on the API.
param searchName string
param searchPrincipalId string
param storageName string
param foundryName string
param readerPrincipalIds array
@description('The deployer, who creates the indexes and knowledge bases. Empty: no assignment.')
param deployerPrincipalId string

var storageBlobDataReader = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var cognitiveServicesOpenAIUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var searchIndexDataReader = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
var searchServiceContributor = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
var searchIndexDataContributor = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'

module readsStorage 'role-storage.bicep' = {
  params: {
    resourceName: storageName
    principalId: searchPrincipalId
    roleDefinitionId: storageBlobDataReader
    principalType: 'ServicePrincipal'
  }
}

// Embeddings and, through OpenAI/responses/*, planning on a GPT-6 model.
module usesFoundry 'role-foundry.bicep' = {
  params: {
    resourceName: foundryName
    principalId: searchPrincipalId
    roleDefinitionId: cognitiveServicesOpenAIUser
    principalType: 'ServicePrincipal'
  }
}

module readers 'role-search.bicep' = [
  for principalId in readerPrincipalIds: {
    params: {
      resourceName: searchName
      principalId: principalId
      roleDefinitionId: searchIndexDataReader
      principalType: 'ServicePrincipal'
    }
  }
]

module deployer 'role-search.bicep' = [
  for role in [searchServiceContributor, searchIndexDataContributor]: if (!empty(deployerPrincipalId)) {
    params: {
      resourceName: searchName
      principalId: deployerPrincipalId
      roleDefinitionId: role
      principalType: 'User'
    }
  }
]
