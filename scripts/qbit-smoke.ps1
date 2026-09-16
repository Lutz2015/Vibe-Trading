param(
  [string]$BaseUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"
foreach ($Path in @("/qbit/health", "/qbit/ops/status", "/qbit/ledger/snapshot", "/qbit/automation/status", "/qbit/strategy/repository")) {
  Write-Host "GET $Path"
  Invoke-RestMethod -Method Get -Uri "$BaseUrl$Path" | ConvertTo-Json -Depth 8
}
