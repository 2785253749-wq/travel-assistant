$ErrorActionPreference = "Stop"

function Test-PlaceholderValue {
    param([string]$Value)

    $normalized = $Value.Trim().Trim('"').Trim("'").Trim()
    if ([string]::IsNullOrWhiteSpace($normalized)) {
        return $true
    }

    return (
        $normalized -match '^(?i:your_|replace_|replace-|example(?:$|[_-])|placeholder|test-only-key$|test-key$|redacted$|masked$|\*+$|<your_[a-z0-9_]+>)' -or
        $normalized -match '^\$\{?[A-Z][A-Z0-9_]*\}?$' -or
        $normalized -match '^%[A-Z][A-Z0-9_]*%$'
    )
}

function Get-SensitiveAssignments {
    param(
        [string]$Content,
        [string]$Name,
        [bool]$AllowColon
    )

    $escapedName = [regex]::Escape($Name)
    $separator = if ($AllowColon) { '[:=]' } else { '=' }
    $pattern = "(?im)(?:^|[,{;])\s*(?:(?:export|const|let|var)\s+)?[`"']?$escapedName[`"']?\s*$separator\s*(?<value>`"[^`"`r`n]*`"|'[^'`r`n]*'|[^\s,#;}]+)"
    return [regex]::Matches($Content, $pattern)
}

$gitStartInfo = New-Object System.Diagnostics.ProcessStartInfo
$gitStartInfo.FileName = "git"
$gitStartInfo.Arguments = "ls-files -z"
$gitStartInfo.UseShellExecute = $false
$gitStartInfo.CreateNoWindow = $true
$gitStartInfo.RedirectStandardOutput = $true
$gitStartInfo.RedirectStandardError = $true
$gitStartInfo.StandardOutputEncoding = [System.Text.Encoding]::UTF8

$gitProcess = New-Object System.Diagnostics.Process
$gitProcess.StartInfo = $gitStartInfo
[void]$gitProcess.Start()
$trackedOutput = $gitProcess.StandardOutput.ReadToEnd()
$gitError = $gitProcess.StandardError.ReadToEnd()
$gitProcess.WaitForExit()

if ($gitProcess.ExitCode -ne 0) {
    Write-Output "Public repository check failed: git ls-files could not be read."
    if (-not [string]::IsNullOrWhiteSpace($gitError)) {
        Write-Output $gitError.Trim()
    }
    exit 2
}
$trackedFiles = $trackedOutput -split [char]0 | Where-Object { $_.Length -gt 0 }

$violations = [System.Collections.Generic.List[string]]::new()

foreach ($file in $trackedFiles) {
    $normalizedPath = $file.Replace('\', '/')
    $allowedSuperpowersReport =
        $normalizedPath -match '^\.superpowers/sdd/\d{4}-\d{2}-\d{2}-[a-z0-9-]+/[a-z0-9-]+-report\.md$'
    $forbiddenPath =
        (($normalizedPath -match '(^|/)\.env($|\.)') -and ($normalizedPath -notmatch '(^|/)\.env\.example$')) -or
        ($normalizedPath -match '(^|/)\.(venv|pytest_cache|mypy_cache|ruff_cache|agents|codex|claude|idea|vscode|worktrees)(/|$)') -or
        (($normalizedPath -match '(^|/)\.superpowers(/|$)') -and (-not $allowedSuperpowersReport)) -or
        ($normalizedPath -match '(^|/)(__pycache__|node_modules|build|dist|htmlcov)(/|$)') -or
        ($normalizedPath -match '(^|/)skills-lock\.json$') -or
        ($normalizedPath -match '(^|/)\.coverage(?:\.|$)') -or
        ($normalizedPath -match '\.(pyc|pyo|db|sqlite|sqlite3|log|key|p12|pfx)$')

    if ($forbiddenPath) {
        $violations.Add("Forbidden tracked path: $normalizedPath")
        continue
    }

    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
        continue
    }

    try {
        $content = [System.IO.File]::ReadAllText((Resolve-Path -LiteralPath $file))
    }
    catch {
        $violations.Add("Unreadable tracked file: $normalizedPath")
        continue
    }

    $allowColonAssignments =
        [System.IO.Path]::GetExtension($normalizedPath).ToLowerInvariant() -in
        @('.json', '.yaml', '.yml', '.toml', '.js', '.jsx', '.ts', '.tsx', '.ini', '.cfg', '.conf', '.properties')
    $sensitiveAssignments = @{}
    $sensitiveAssignments[("DEEPSEEK_API" + "_KEY")] = "DeepSeek API key"
    $sensitiveAssignments[("SUPABASE_SERVICE" + "_KEY")] = "Supabase service key"
    $sensitiveAssignments[("ANON_SESSION_SIGNING" + "_SECRET")] = "anonymous session signing secret"
    foreach ($name in $sensitiveAssignments.Keys) {
        foreach ($match in (Get-SensitiveAssignments -Content $content -Name $name -AllowColon $allowColonAssignments)) {
            if (-not (Test-PlaceholderValue $match.Groups["value"].Value)) {
                $violations.Add("Credential pattern ($($sensitiveAssignments[$name])): $normalizedPath")
                break
            }
        }
    }

    if ($content -match '(?<![A-Za-z0-9_])[s][k]-[A-Za-z0-9_-]{20,}') {
        $violations.Add("Credential pattern (raw secret token): $normalizedPath")
    }

    if ($content -match '(?<![A-Za-z0-9_])[s][b]_secret_[A-Za-z0-9_-]{20,}') {
        $violations.Add("Credential pattern (Supabase secret token): $normalizedPath")
    }

    if ($content -match '(?<![A-Za-z0-9_-])[e][y][J][A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(?![A-Za-z0-9_-])') {
        $violations.Add("Credential pattern (raw JWT): $normalizedPath")
    }

    if ($content -match '(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{20,}' -or
        $content -match '(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{20,}') {
        $violations.Add("Credential pattern (GitHub token): $normalizedPath")
    }

    if ($content -match '-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----') {
        $violations.Add("Credential pattern (private key): $normalizedPath")
    }
}

if ($violations.Count -gt 0) {
    Write-Output "Public repository check failed:"
    $violations | Sort-Object -Unique | ForEach-Object { Write-Output "- $_" }
    exit 1
}

Write-Output "Public repository check passed"
exit 0
