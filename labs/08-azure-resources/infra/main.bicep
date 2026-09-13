targetScope = 'resourceGroup'

@description('Deployment environment used in names, tags, and policy choices.')
@allowed([
  'dev'
  'staging'
  'prod'
])
param environment string

@description('Azure region for relay resources.')
param location string = resourceGroup().location

@description('Short lowercase prefix for globally named resources.')
@minLength(3)
@maxLength(10)
param namePrefix string = 'relay'

@description('Kubernetes version approved for this relay release.')
param kubernetesVersion string

var commonTags = {
  service: 'relay'
  api: '/tasks'
  environment: environment
  managedBy: 'bicep'
}

module platform 'modules/platform.bicep' = {
  name: 'relay-platform-${environment}'
  params: {
    environment: environment
    location: location
    namePrefix: namePrefix
    kubernetesVersion: kubernetesVersion
    tags: commonTags
  }
}

module storage 'modules/storage.bicep' = {
  name: 'relay-storage-${environment}'
  params: {
    environment: environment
    location: location
    namePrefix: namePrefix
    workloadPrincipalId: platform.outputs.workloadPrincipalId
    tags: commonTags
  }
}

output aksName string = platform.outputs.aksName
output acrLoginServer string = platform.outputs.acrLoginServer
output workloadClientId string = platform.outputs.workloadClientId
output storageBlobEndpoint string = storage.outputs.blobEndpoint
output taskQueueName string = storage.outputs.taskQueueName
