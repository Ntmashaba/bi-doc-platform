// BI Documentation Platform library on Azure Container Apps (B13; handoff 11).
//
// Pilot defaults: Consumption plan, 0-1 replicas, 0.25 vCPU / 0.5 GiB, a user-assigned
// managed identity with data-plane roles on one storage account (no keys), logs in Log
// Analytics, and a monthly budget alert. No AKS, database server, paid search or VM.
// Budget alerts notify; they do not cap spending.
//
// Deploy (see docs/azure-deployment.md):
//   az deployment group create -g <rg> -f deploy/azure/main.bicep -p @deploy/azure/example.parameters.json

targetScope = 'resourceGroup'

@description('Short lowercase prefix for resource names (3-11 letters or digits).')
@minLength(3)
@maxLength(11)
param namePrefix string

param location string = resourceGroup().location

@description('Library container image, e.g. myregistry.azurecr.io/bidoc-library:0.1.0 (build it from the repository Dockerfile).')
param containerImage string

@description('Registry login server when the image is private in Azure Container Registry; the identity gets AcrPull on it. Empty for a public image.')
param registryServer string = ''

@description('Name of an existing Azure Container Registry in this resource group (for the AcrPull role). Empty when registryServer is empty.')
param registryName string = ''

@description('Existing storage account in this resource group to use. Empty creates a new one.')
param existingStorageAccountName string = ''

param tableName string = 'bidoc'
param containerName string = 'bidoc'

@description('Entra ID app registration (client) ID used for sign-in.')
param entraClientId string

@description('Client secret of the app registration (stored as a Container App secret).')
@secure()
param entraClientSecret string

@description('Map from Entra app role values to library roles.')
param entraRoleMap string = 'BiDoc.Viewer=viewer,BiDoc.Publisher=publisher,BiDoc.Admin=admin'

@description('Library roles for signed-in users with no app role. Empty: they are refused (403).')
param entraDefaultRoles string = ''

@description('Addresses the platform authentication forwards from. Verify during go-live (docs/azure-deployment.md, step 6).')
param trustedProxies string = '127.0.0.1/32,::1/128'

@minValue(0)
@maxValue(1)
param minReplicas int = 0

@minValue(1)
@maxValue(1)
@description('The pilot runs one replica: the Azure store is safe under concurrency, but the in-process derived rebuild is sized for one.')
param maxReplicas int = 1

param cpu string = '0.25'
param memory string = '0.5Gi'

@minValue(30)
@maxValue(730)
param logRetentionDays int = 30

@description('Monthly budget in the billing currency. 0 skips the budget.')
param monthlyBudget int = 20

param budgetContactEmails array = []

@description('First day of the budget period (yyyy-MM-01).')
param budgetStartDate string = utcNow('yyyy-MM-01')

param tags object = {}

var suffix = uniqueString(resourceGroup().id, namePrefix)
var storageName = empty(existingStorageAccountName) ? take('${namePrefix}${suffix}', 24) : existingStorageAccountName
var roles = {
  tableDataContributor: '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
  blobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-library-id'
  location: location
  tags: tags
}

resource newStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = if (empty(existingStorageAccountName)) {
  name: storageName
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false          // managed identity only; no account keys in use
    defaultToOAuthAuthentication: true
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageName
  dependsOn: [ newStorage ]
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: { enabled: true, days: 14 }          // blobs are immutable; this guards operator mistakes
    containerDeleteRetentionPolicy: { enabled: true, days: 14 }
  }
}

resource blobContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: containerName
  properties: { publicAccess: 'None' }
}

resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource table 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: tableName
}

resource tableRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, identity.id, roles.tableDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.tableDataContributor)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource blobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, identity.id, roles.blobDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataContributor)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = if (!empty(registryName)) {
  name: empty(registryName) ? 'unused' : registryName
}

resource pullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(registryName)) {
  scope: registry
  name: guid(resourceGroup().id, registryName, identity.id, roles.acrPull)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-logs'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: logRetentionDays
  }
}

resource appEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${namePrefix}-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-library'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    managedEnvironmentId: appEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8765
        transport: 'auto'
        allowInsecure: false             // http is redirected to https at the ingress
      }
      registries: empty(registryServer) ? [] : [
        { server: registryServer, identity: identity.id }
      ]
      secrets: [
        { name: 'entra-client-secret', value: entraClientSecret }
      ]
    }
    template: {
      containers: [
        {
          name: 'library'
          image: containerImage
          resources: { cpu: json(cpu), memory: memory }
          env: [
            { name: 'DATA_BACKEND', value: 'azure' }
            { name: 'AZURE_STORAGE_TABLE_ENDPOINT', value: storage.properties.primaryEndpoints.table }
            { name: 'AZURE_STORAGE_BLOB_ENDPOINT', value: storage.properties.primaryEndpoints.blob }
            { name: 'AZURE_STORAGE_TABLE', value: tableName }
            { name: 'AZURE_STORAGE_CONTAINER', value: containerName }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'AUTH_MODE', value: 'entra' }
            { name: 'ENTRA_TENANT_ID', value: tenant().tenantId }
            { name: 'ENTRA_ROLE_MAP', value: entraRoleMap }
            { name: 'ENTRA_DEFAULT_ROLES', value: entraDefaultRoles }
            { name: 'GATEWAY_TRUSTED_PROXIES', value: trustedProxies }
            { name: 'BIND_HOST', value: '0.0.0.0' }
          ]
          probes: [
            { type: 'Liveness', httpGet: { path: '/api/v1/health/live', port: 8765 }, periodSeconds: 30 }
            { type: 'Readiness', httpGet: { path: '/api/v1/health/ready', port: 8765 }, periodSeconds: 10 }
            { type: 'Startup', httpGet: { path: '/api/v1/health/live', port: 8765 }, periodSeconds: 2, failureThreshold: 30 }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          { name: 'http', http: { metadata: { concurrentRequests: '20' } } }
        ]
      }
    }
  }
  dependsOn: [ tableRole, blobRole, pullRole, table, blobContainer ]
}

// Built-in authentication (Easy Auth) with Microsoft Entra ID. Everything except health
// probes and the token-only publishing API needs sign-in; the library then reads the
// validated principal (AUTH_MODE=entra). The publishing API rejects anything but a
// publishing token, so leaving it outside sign-in opens nothing else (handoff 17.5).
resource auth 'Microsoft.App/containerApps/authConfigs@2024-03-01' = {
  parent: app
  name: 'current'
  properties: {
    platform: { enabled: true }
    globalValidation: {
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'azureactivedirectory'
      excludedPaths: [
        '/api/v1/health/live'
        '/api/v1/health/ready'
        '/api/v1/publishing/*'
      ]
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: entraClientId
          clientSecretSettingName: 'entra-client-secret'
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenant().tenantId}/v2.0'
        }
        validation: {
          allowedAudiences: [ entraClientId, 'api://${entraClientId}' ]
        }
      }
    }
    login: {
      preserveUrlFragmentsForLogins: true     // the library uses #/ routes
    }
  }
}

resource budget 'Microsoft.Consumption/budgets@2023-11-01' = if (monthlyBudget > 0 && !empty(budgetContactEmails)) {
  name: '${namePrefix}-library-budget'
  properties: {
    category: 'Cost'
    amount: monthlyBudget
    timeGrain: 'Monthly'
    timePeriod: { startDate: budgetStartDate }
    notifications: {
      actual80: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 80
        thresholdType: 'Actual'
        contactEmails: budgetContactEmails
      }
      forecast100: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 100
        thresholdType: 'Forecasted'
        contactEmails: budgetContactEmails
      }
    }
  }
}

output url string = 'https://${app.properties.configuration.ingress.fqdn}'
output redirectUri string = 'https://${app.properties.configuration.ingress.fqdn}/.auth/login/aad/callback'
output storageAccount string = storageName
output identityClientId string = identity.properties.clientId
