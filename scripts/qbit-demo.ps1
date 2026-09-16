param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [double]$InitialCash = 1000000
)

$ErrorActionPreference = "Stop"
$Headers = @{ "Content-Type" = "application/json" }

Invoke-RestMethod -Method Post -Uri "$BaseUrl/qbit/ledger/reset" -Headers $Headers -Body (@{ initial_cash = $InitialCash } | ConvertTo-Json)
Invoke-RestMethod -Method Post -Uri "$BaseUrl/qbit/automation/run-cycle" -Headers $Headers -Body (@{ force = $true; rebalance = $true } | ConvertTo-Json)
Invoke-RestMethod -Method Get -Uri "$BaseUrl/qbit/ledger/snapshot" | ConvertTo-Json -Depth 8
