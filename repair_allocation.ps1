param([switch]$Undo,[string]$ScriptPath=$PSCommandPath)
$ErrorActionPreference = 'Stop'
$repairRoot = Split-Path -Parent $ScriptPath
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $quoted = "'" + $ScriptPath.Replace("'","''") + "'"
        $command = '& ([scriptblock]::Create([IO.File]::ReadAllText(' + $quoted + '))) -ScriptPath ' + $quoted
        if ($Undo) { $command += ' -Undo' }
        $arguments = '-NoProfile -EncodedCommand ' + [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        Start-Process -FilePath powershell.exe -Verb RunAs -ArgumentList $arguments -WindowStyle Hidden -Wait
        Get-Content -LiteralPath (Join-Path $repairRoot 'data\pagefile-repair-result.txt')
        return
    }
    $key = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management'
    $backup = Join-Path $repairRoot 'data\pagefile-before-repair.json'
    $current = @((Get-ItemProperty -LiteralPath $key -Name PagingFiles).PagingFiles)
    if ($Undo) {
        if (-not (Test-Path -LiteralPath $backup)) { throw 'No saved setting exists. Nothing changed.' }
        $saved = Get-Content -LiteralPath $backup -Raw | ConvertFrom-Json
        if (@($saved.applied).Count -ne $current.Count -or (Compare-Object @($saved.applied) $current)) {
            throw 'The page-file setting changed outside this repair. Undo stopped to preserve that change.'
        }
        $replacement = [string[]]@($saved.original)
    } else {
        $drive = Get-PSDrive C
        if ($drive.Free -lt 144GB) { throw 'Less than 144 GB is free on C:. Nothing changed.' }
        $replacement = [string[]](@($current | Where-Object { $_ -notmatch '(?i)^c:\\pagefile\.sys(?:\s|$)' }) + 'C:\pagefile.sys 131072 196608')
        if ($current | Where-Object { $_ -match '^\?:' }) { throw 'An automatic multi-drive page file is configured. Manual review is required; nothing changed.' }
        if (Test-Path -LiteralPath $backup) {
            $saved = Get-Content -LiteralPath $backup -Raw | ConvertFrom-Json
            if (@($saved.applied).Count -eq $current.Count -and -not (Compare-Object @($saved.applied) $current)) {
                throw 'This repair is already configured. Restart Windows if you have not done so.'
            }
            if (@($saved.original).Count -ne $current.Count -or (Compare-Object @($saved.original) $current)) {
                throw 'The page-file setting changed since the saved backup. Manual review is required.'
            }
        } else {
            New-Item -ItemType Directory -Path (Split-Path $backup) -Force | Out-Null
            @{original=$current;applied=$replacement;time=(Get-Date -Format o)} | ConvertTo-Json | Set-Content -LiteralPath $backup -Encoding UTF8
        }
    }
    Set-ItemProperty -LiteralPath $key -Name PagingFiles -Type MultiString -Value $replacement
    $actual = @((Get-ItemProperty -LiteralPath $key -Name PagingFiles).PagingFiles)
    if ($actual.Count -ne $replacement.Count -or (Compare-Object $replacement $actual)) { throw 'Windows did not retain the requested setting.' }
    $result = 'SUCCESS: Page-file setting saved. Graphics memory unchanged. Save your work and restart Windows yourself. Then use Assistant > Explain my memory to verify allocation room.'
} catch {
    $result = 'REPAIR NOT COMPLETED: ' + $_.Exception.Message
}
New-Item -ItemType Directory -Path (Join-Path $repairRoot 'data') -Force | Out-Null
$result | Set-Content -LiteralPath (Join-Path $repairRoot 'data\pagefile-repair-result.txt') -Encoding UTF8
Write-Host $result
