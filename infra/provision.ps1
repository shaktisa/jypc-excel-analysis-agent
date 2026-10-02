<#
.SYNOPSIS
    Provisions the Excel Analysis Agent in the PLG Hackathon subscription.

.DESCRIPTION
    Idempotent — safe to re-run. Creates NO secrets anywhere:

      * the web app authenticates to ACR, Blob Storage and Azure OpenAI with a
        user-assigned managed identity;
      * GitHub Actions authenticates with a second user-assigned managed identity
        plus an OIDC federated credential, so no client secret is ever issued.

    Existing shared resources are reused (ACR, storage account, App Insights).
    A dedicated App Service plan is created so this app cannot destabilise the
    sfi-hygiene apps already running on sfi-hygiene-plan.

.NOTES
    Requires Owner (or User Access Administrator + Contributor) on the
    subscription in order to create the role assignments.
#>
[CmdletBinding()]
param(
    [string] $SubscriptionId = '1d09b2fc-73dd-4a82-8e90-9f5edaa4f18b',
    [string] $ResourceGroup  = 'ExDGrowthCommercialCopilot',
    [string] $Location       = 'westus2',

    [string] $AppName        = 'jypc-excel-analysis-agent',
    [string] $PlanName       = 'jypc-excel-agent-plan',
    [string] $PlanSku        = 'B1',

    [string] $RuntimeIdentity = 'jypc-excel-agent-mi',
    [string] $DeployIdentity  = 'jypc-excel-agent-cicd-mi',

    [string] $AcrName         = 'copilotanalytixacr',
    [string] $StorageAccount  = 'copilotanalytixsa',
    [string] $BlobContainer   = 'excel-agent',
    [string] $AppInsights     = 'copilotanalytix-appinsights',

    # Lives in the same resource group, so we can grant the runtime identity
    # access to it. (exdgrowth-ai-foundry is in a resource group where we do
    # not hold Microsoft.Authorization/roleAssignments/write.)
    [string] $OpenAiAccount   = 'commercial-growth-openai',
    [string] $OpenAiGroup     = 'ExDGrowthCommercialCopilot',
    [string] $OpenAiEndpoint  = 'https://commercial-growth-openai.openai.azure.com/',
    [string] $OpenAiDeployment = 'commercial-growth-gpt-4.1',

    [string] $GitHubOwner     = 'shaktisa',
    [string] $GitHubRepo      = 'jypc-excel-analysis-agent',
    [string] $GitHubBranch    = 'main',

    [string] $ImageTag        = 'latest'
)

$ErrorActionPreference = 'Stop'

function Step([string] $Message) {
    Write-Host ''
    Write-Host "==> $Message" -ForegroundColor Cyan
}

Step "Selecting subscription $SubscriptionId"
az account set --subscription $SubscriptionId | Out-Null

# --------------------------------------------------------------------------- #
# 1. Managed identities
# --------------------------------------------------------------------------- #
Step 'Creating managed identities (idempotent)'
az identity create -g $ResourceGroup -n $RuntimeIdentity -l $Location --only-show-errors | Out-Null
az identity create -g $ResourceGroup -n $DeployIdentity  -l $Location --only-show-errors | Out-Null

$runtime = az identity show -g $ResourceGroup -n $RuntimeIdentity -o json | ConvertFrom-Json
$deploy  = az identity show -g $ResourceGroup -n $DeployIdentity  -o json | ConvertFrom-Json

Write-Host "    runtime  clientId=$($runtime.clientId)"
Write-Host "    deploy   clientId=$($deploy.clientId)"

# --------------------------------------------------------------------------- #
# 2. GitHub OIDC federated credential on the deploy identity (no secrets)
# --------------------------------------------------------------------------- #
Step 'Configuring the GitHub OIDC federated credential'
$subject = "repo:${GitHubOwner}/${GitHubRepo}:ref:refs/heads/${GitHubBranch}"
$existing = az identity federated-credential list -g $ResourceGroup `
    --identity-name $DeployIdentity -o json | ConvertFrom-Json

if (-not ($existing | Where-Object { $_.subject -eq $subject })) {
    az identity federated-credential create `
        -g $ResourceGroup --identity-name $DeployIdentity -n 'github-main' `
        --issuer 'https://token.actions.githubusercontent.com' `
        --subject $subject `
        --audiences 'api://AzureADTokenExchange' | Out-Null
    Write-Host "    created: $subject"
} else {
    Write-Host "    already present: $subject"
}

# --------------------------------------------------------------------------- #
# 3. Role assignments — least privilege, no keys anywhere
# --------------------------------------------------------------------------- #
Step 'Assigning roles'

$acrId     = az acr show -n $AcrName -g $ResourceGroup --query id -o tsv
$storageId = az storage account show -n $StorageAccount -g $ResourceGroup --query id -o tsv
$openAiId  = az cognitiveservices account show -n $OpenAiAccount -g $OpenAiGroup --query id -o tsv
$rgId      = az group show -n $ResourceGroup --query id -o tsv

function Grant([string] $PrincipalId, [string] $Role, [string] $Scope) {
    # NOTE: 'az role assignment list' does not accept --assignee-principal-type;
    # only 'create' does. Passing it to list silently breaks the idempotency check.
    $hit = az role assignment list --assignee $PrincipalId `
        --role $Role --scope $Scope --query "[0].id" -o tsv
    if ([string]::IsNullOrWhiteSpace($hit)) {
        $created = az role assignment create --assignee-object-id $PrincipalId `
            --assignee-principal-type ServicePrincipal --role $Role --scope $Scope `
            --query roleDefinitionName -o tsv
        if ([string]::IsNullOrWhiteSpace($created)) {
            Write-Warning "    FAILED to grant '$Role' at $Scope - check your own permissions."
        } else {
            Write-Host "    granted  $Role"
        }
    } else {
        Write-Host "    present  $Role"
    }
}

# Runtime: pull its own image, read/write workbooks, call the model.
Grant $runtime.principalId 'AcrPull'                       $acrId
Grant $runtime.principalId 'Storage Blob Data Contributor'  $storageId
Grant $runtime.principalId 'Cognitive Services OpenAI User' $openAiId

# CI/CD: build+push images and restart the web app.
Grant $deploy.principalId  'AcrPush'                        $acrId
Grant $deploy.principalId  'Contributor'                    $rgId

# --------------------------------------------------------------------------- #
# 4. Blob container for workbook versions
# --------------------------------------------------------------------------- #
Step "Ensuring blob container '$BlobContainer'"
az storage container create --name $BlobContainer --account-name $StorageAccount `
    --auth-mode login --only-show-errors | Out-Null

# --------------------------------------------------------------------------- #
# 5. App Service plan + web app
# --------------------------------------------------------------------------- #
Step "Ensuring App Service plan '$PlanName' ($PlanSku, Linux)"
az appservice plan create -g $ResourceGroup -n $PlanName --is-linux --sku $PlanSku `
    -l $Location --only-show-errors | Out-Null

$image = "$AcrName.azurecr.io/${AppName}:$ImageTag"

Step "Ensuring web app '$AppName'"
$site = az webapp show -g $ResourceGroup -n $AppName -o json 2>$null
if (-not $site) {
    az webapp create -g $ResourceGroup -p $PlanName -n $AppName `
        --container-image-name $image --only-show-errors | Out-Null
    Write-Host '    created'
} else {
    Write-Host '    already exists'
}

Step 'Attaching the runtime managed identity'
az webapp identity assign -g $ResourceGroup -n $AppName --identities $runtime.id --only-show-errors | Out-Null

Step 'Pointing the web app at ACR using managed-identity pull (no admin credentials)'
# 'az webapp config set --generic-configurations' takes a JSON string that
# PowerShell mangles on the way through; patch the config resource directly.
$siteId = az webapp show -g $ResourceGroup -n $AppName --query id -o tsv
az resource update --ids "$siteId/config/web" `
    --set properties.acrUseManagedIdentityCreds=true `
          properties.acrUserManagedIdentityID=$($runtime.clientId) `
    --only-show-errors | Out-Null
az webapp config container set -g $ResourceGroup -n $AppName `
    --container-image-name $image --container-registry-url "https://$AcrName.azurecr.io" `
    --only-show-errors | Out-Null

# --------------------------------------------------------------------------- #
# 6. App settings — endpoints and ids only, never a key
# --------------------------------------------------------------------------- #
Step 'Applying app settings'
$aiConnection = az resource show -g $ResourceGroup -n $AppInsights `
    --resource-type 'microsoft.insights/components' --query 'properties.ConnectionString' -o tsv

az webapp config appsettings set -g $ResourceGroup -n $AppName --settings `
    "AZURE_CLIENT_ID=$($runtime.clientId)" `
    "AZURE_OPENAI_ENDPOINT=$OpenAiEndpoint" `
    "AZURE_OPENAI_DEPLOYMENT=$OpenAiDeployment" `
    "AZURE_STORAGE_ACCOUNT=$StorageAccount" `
    "AZURE_BLOB_CONTAINER=$BlobContainer" `
    "APPLICATIONINSIGHTS_CONNECTION_STRING=$aiConnection" `
    "ENABLE_WRITE_TOOLS=false" `
    "ENV_LABEL=PROD" `
    "WEBSITES_PORT=8000" `
    "PORT=8000" `
    --only-show-errors | Out-Null

az webapp config set -g $ResourceGroup -n $AppName --always-on true --only-show-errors | Out-Null

# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #
$hostName = az webapp show -g $ResourceGroup -n $AppName --query defaultHostName -o tsv

Write-Host ''
Write-Host '────────────────────────────────────────────────────────────' -ForegroundColor Green
Write-Host " Public URL       https://$hostName"                          -ForegroundColor Green
Write-Host '────────────────────────────────────────────────────────────' -ForegroundColor Green
Write-Host ''
Write-Host 'Add these as GitHub repository variables (they are identifiers, not secrets):'
Write-Host "  AZURE_CLIENT_ID       $($deploy.clientId)"
Write-Host "  AZURE_TENANT_ID       $(az account show --query tenantId -o tsv)"
Write-Host "  AZURE_SUBSCRIPTION_ID $SubscriptionId"
