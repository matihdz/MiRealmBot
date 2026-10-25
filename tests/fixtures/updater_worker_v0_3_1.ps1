param([Parameter(Mandatory=$true)][string]$Plan)
# Runs outside the installed folder. Never removes a directory or kills a process.
$ErrorActionPreference = 'Stop'
$result = @{ ok = $false; rollback = $false }
$oldMoved = $false
$newMoved = $false
$planData = $null
try {
    $planData = Get-Content -LiteralPath $Plan -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($planData.format -ne 1) { throw 'Unknown update plan format.' }
    $appRoot = [IO.Path]::GetFullPath([string]$planData.app_root).TrimEnd('\')
    $workspace = [IO.Path]::GetFullPath([string]$planData.workspace).TrimEnd('\')
    $staged = [IO.Path]::GetFullPath([string]$planData.staged_root).TrimEnd('\')
    $backup = [IO.Path]::GetFullPath([string]$planData.backup_root).TrimEnd('\')
    $parentFolder = [IO.Path]::GetDirectoryName($appRoot)
    $appName = [IO.Path]::GetFileName($appRoot)
    if ([IO.Path]::GetDirectoryName($workspace) -ne $parentFolder -or
        -not [IO.Path]::GetFileName($workspace).StartsWith('.MiRealmBot-update-') -or
        $staged -ne [IO.Path]::Combine($workspace, 'staged', 'MiRealmBot') -or
        [IO.Path]::GetDirectoryName($backup) -ne $parentFolder -or
        -not [IO.Path]::GetFileName($backup).StartsWith($appName + '.backup-') -or
        [IO.Path]::GetFullPath($Plan) -ne [IO.Path]::Combine($workspace, 'plan.json')) {
        throw 'Update paths do not belong to this portable installation.'
    }
    if (-not [IO.File]::Exists([IO.Path]::Combine($appRoot, 'MiRealmBot.exe')) -or
        -not [IO.Directory]::Exists([IO.Path]::Combine($appRoot, '_internal')) -or
        [IO.Directory]::Exists($backup)) { throw 'Installation or backup path is not ready.' }
    $manifest = Get-Content -LiteralPath ([IO.Path]::Combine($staged, 'BUILD-MANIFEST.json')) -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.application -ne 'MiRealmBot' -or $manifest.version -ne $planData.version -or
        -not [IO.File]::Exists([IO.Path]::Combine($staged, 'MiRealmBot.exe')) -or
        -not [IO.Directory]::Exists([IO.Path]::Combine($staged, '_internal'))) { throw 'Staged release is incomplete.' }
    if ([int]$planData.parent_pid -gt 0) {
        try { $parentProcess = [Diagnostics.Process]::GetProcessById([int]$planData.parent_pid) }
        catch [ArgumentException] { $parentProcess = $null }
        if ($null -ne $parentProcess) {
            try {
                # Open the handle before checking creation time; retain that same
                # process handle while waiting, even if the PID is later reused.
                $null = $parentProcess.Handle
                if ($parentProcess.StartTime.ToUniversalTime().Ticks -ne [long]$planData.parent_started_ticks) {
                    throw 'The process identity changed. No files were replaced.'
                }
                if (-not $parentProcess.WaitForExit(120000)) { throw 'MiRealmBot did not close within two minutes. No files were replaced.' }
            } finally { $parentProcess.Dispose() }
        }
    }
    # Replace release-owned files as a complete tree. Keep local configuration,
    # logs and unknown top-level personal files; the entire old tree is backed up.
    $releaseOwned = @('MiRealmBot.exe', '_internal', 'BUILD-MANIFEST.json', 'autonexus.json',
        'LEEME-PRIMERO.txt', 'docs', 'licenses', 'THIRD-PARTY-NOTICES.txt', 'LICENSE')
    foreach ($item in Get-ChildItem -LiteralPath $appRoot -Force) {
        if ($item.Name -eq 'autonexus.json' -or $releaseOwned -notcontains $item.Name) {
            $destination = [IO.Path]::Combine($staged, $item.Name)
            if ($item.Name -ne 'autonexus.json' -and (Test-Path -LiteralPath $destination)) {
                throw ('A personal file conflicts with the new release: ' + $item.Name)
            }
            Copy-Item -LiteralPath $item.FullName -Destination $destination -Recurse -Force
        }
    }
    [IO.Directory]::Move($appRoot, $backup)
    $oldMoved = $true
    [IO.Directory]::Move($staged, $appRoot)
    $newMoved = $true
    if ($planData.restart) {
        # This is the user-facing interface being reopened, so show its window.
        $newProcess = Start-Process -FilePath ([IO.Path]::Combine($appRoot, 'MiRealmBot.exe')) -WorkingDirectory $appRoot -PassThru
        $newProcess.Dispose()
    }
    $result.ok = $true
    $result.backup_root = $backup
    $result.version = $planData.version
} catch {
    $result.error = $_.Exception.Message
    if ($oldMoved) {
        try {
            if ($newMoved) { [IO.Directory]::Move($appRoot, [IO.Path]::Combine($workspace, 'failed-new-installation')) }
            [IO.Directory]::Move($backup, $appRoot)
            $result.rollback = $true
        } catch { $result.rollback_error = $_.Exception.Message }
    }
} finally {
    if ($null -ne $planData -and $null -ne $workspace -and [IO.Directory]::Exists($workspace)) {
        $result.finished_at_utc = [DateTime]::UtcNow.ToString('o')
        $result | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath ([IO.Path]::Combine($workspace, 'result.json')) -Encoding UTF8
    }
}
if ($result.ok) { exit 0 }
if ($null -ne $planData -and $planData.restart) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $notice = 'The update could not finish. ' + $result.error
        if ($result.rollback) { $notice += "`nThe previous installation was restored." }
        $notice += "`nDetails: " + [IO.Path]::Combine($workspace, 'result.json')
        $null = [Windows.Forms.MessageBox]::Show($notice, 'MiRealmBot - update', 'OK', 'Error')
    } catch { }
}
exit 1
