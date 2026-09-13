@minLength(3)
param environment string
param location string
@minLength(3)
param namePrefix string
param kubernetesVersion string
param tags object

var suffix = toLower(uniqueString(subscription().id, resourceGroup().id, environment))
var registryName = take('${namePrefix}${environment}${suffix}', 50)
var clusterName = '${namePrefix}-${environment}-aks'
var workloadIdentityName = '${namePrefix}-${environment}-workload'

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  tags: tags
  sku: {
    name: environment == 'prod' ? 'Premium' : 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: environment == 'prod' ? 'Disabled' : 'Enabled'
  }
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-${environment}-logs'
  location: location
  tags: tags
  properties: {
    retentionInDays: environment == 'prod' ? 90 : 30
  }
}

resource workloadIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: workloadIdentityName
  location: location
  tags: tags
}

resource cluster 'Microsoft.ContainerService/managedClusters@2024-05-01' = {
  name: clusterName
  location: location
  tags: tags
  sku: {
    name: 'Base'
    tier: environment == 'prod' ? 'Standard' : 'Free'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    kubernetesVersion: kubernetesVersion
    dnsPrefix: '${namePrefix}-${environment}'
    enableRBAC: true
    disableLocalAccounts: true
    oidcIssuerProfile: {
      enabled: true
    }
    securityProfile: {
      workloadIdentity: {
        enabled: true
      }
    }
    addonProfiles: {
      omsagent: {
        enabled: true
        config: {
          logAnalyticsWorkspaceResourceID: logs.id
          useAADAuth: 'true'
        }
      }
    }
    agentPoolProfiles: [
      {
        name: 'system'
        count: environment == 'prod' ? 3 : 1
        vmSize: 'Standard_D4ds_v5'
        mode: 'System'
        osType: 'Linux'
        type: 'VirtualMachineScaleSets'
        availabilityZones: environment == 'prod' ? [
          '1'
          '2'
          '3'
        ] : []
        upgradeSettings: {
          maxSurge: '33%'
        }
      }
    ]
    networkProfile: {
      networkPlugin: 'azure'
      networkPolicy: 'azure'
      loadBalancerSku: 'standard'
      outboundType: 'loadBalancer'
    }
  }
}

resource workloadFederation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: workloadIdentity
  name: 'relay-${environment}'
  properties: {
    audiences: [
      'api://AzureADTokenExchange'
    ]
    issuer: cluster.properties.oidcIssuerProfile.issuerURL
    subject: 'system:serviceaccount:relay-${environment}:relay'
  }
}

resource registryPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, cluster.id, 'AcrPull')
  scope: registry
  properties: {
    principalId: cluster.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d'
    )
  }
}

output aksName string = cluster.name
output acrLoginServer string = registry.properties.loginServer
output workloadClientId string = workloadIdentity.properties.clientId
output workloadPrincipalId string = workloadIdentity.properties.principalId
