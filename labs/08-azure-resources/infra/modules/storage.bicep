@minLength(3)
param environment string
param location string
@minLength(3)
param namePrefix string
param workloadPrincipalId string
param tags object

var suffix = toLower(uniqueString(subscription().id, resourceGroup().id, environment))
var storageName = take('${namePrefix}${environment}${suffix}', 24)

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: tags
  sku: {
    name: environment == 'prod' ? 'Standard_ZRS' : 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Enabled'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource taskContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'tasks'
  properties: {
    publicAccess: 'None'
  }
}

resource queueService 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource taskQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  parent: queueService
  name: 'tasks'
}

resource blobDataRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, workloadPrincipalId, 'Storage Blob Data Contributor')
  scope: storage
  properties: {
    principalId: workloadPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
    )
  }
}

resource queueDataRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, workloadPrincipalId, 'Storage Queue Data Contributor')
  scope: storage
  properties: {
    principalId: workloadPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
    )
  }
}

output blobEndpoint string = storage.properties.primaryEndpoints.blob
output taskQueueName string = taskQueue.name
