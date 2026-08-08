$ErrorActionPreference = "Stop"

function Test-PlaceholderValue {
    param([string]$Value)

    $normalized = $Value.Trim().Trim('"').Trim("'").Trim()
    if ([string]::IsNullOrWhiteSpace($normalized)) {
        return $true
    }

    $exactPlaceholders = @(
        'example',
        'placeholder',
        'test-only-key',
        'test-key',
        'redacted',
        'masked',
        'your_deepseek_api_key_here',
        'your_supabase_service_key_here',
        'your_anon_session_signing_secret_here',
        'replace_with_at_least_32_random_characters_in_production',
        '<your_deepseek_api_key>',
        '<your_supabase_service_key>',
        '<your_anon_session_signing_secret>'
    )

    return (
        $exactPlaceholders -contains $normalized.ToLowerInvariant() -or
        $normalized -match '^\*+$'
    )
}

function Test-SafeReference {
    param([string]$Value)

    $normalized = $Value.Trim().Trim('"').Trim("'").Trim()
    return (
        $normalized -match '^\$[A-Z][A-Z0-9_]*$' -or
        $normalized -match '^\$\{[A-Z][A-Z0-9_]*\}$' -or
        $normalized -match '^%[A-Z][A-Z0-9_]*%$' -or
        $normalized -match '^(?i:(?:process|import\.meta)\.env\.[A-Z][A-Z0-9_]*)$' -or
        $normalized -match '^(?i:(?:process|import\.meta)\.env\[(?:"[A-Z][A-Z0-9_]*"|''[A-Z][A-Z0-9_]*'')\])$' -or
        $normalized -match '^(?i:os\.environ\[(?:"[A-Z][A-Z0-9_]*"|''[A-Z][A-Z0-9_]*'')\])$' -or
        $normalized -match '^(?i:(?:os\.getenv|Deno\.env\.get|System\.getenv)\((?:"[A-Z][A-Z0-9_]*"|''[A-Z][A-Z0-9_]*'')\))$'
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
    $receiver = '(?:(?:[A-Za-z_$][A-Za-z0-9_$]*\.)+|\$env:)?'
    $pattern = "(?im)(?:^|[,{;])\s*(?:(?:export|const|let|var)\s+)?(?<key>$receiver[`"']?$escapedName[`"']?)\??\s*(?<separator>$separator)\s*(?<value>`"[^`"`r`n]*`"|'[^'`r`n]*'|[^\s,#;}]+)"
    return [regex]::Matches($Content, $pattern)
}

function Test-TypeScriptTypeMember {
    param(
        [string]$Content,
        [System.Text.RegularExpressions.Match]$Match,
        [string]$Extension
    )

    if ($Extension -notin @('.ts', '.tsx') -or $Match.Groups['separator'].Value -ne ':') {
        return $false
    }

    $keyIndex = $Match.Groups['key'].Index
    $prefix = $Content.Substring(0, $keyIndex)
    $declarationPattern = '(?im)\b(?:interface\s+[A-Za-z_$][A-Za-z0-9_$]*(?:<[^>{}]+>)?(?:\s+extends[^\{]+)?|type\s+[A-Za-z_$][A-Za-z0-9_$]*(?:<[^>{}]+>)?\s*=)\s*\{'
    $declarations = [regex]::Matches($prefix, $declarationPattern)
    for ($index = $declarations.Count - 1; $index -ge 0; $index--) {
        $declaration = $declarations[$index]
        $segment = $Content.Substring($declaration.Index, $keyIndex - $declaration.Index)
        $openCount = ([regex]::Matches($segment, '\{')).Count
        $closeCount = ([regex]::Matches($segment, '\}')).Count
        if ($openCount -gt $closeCount) {
            return $true
        }
    }
    return $false
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

    $extension = [System.IO.Path]::GetExtension($normalizedPath).ToLowerInvariant()
    $allowColonAssignments =
        $extension -in
        @('.json', '.yaml', '.yml', '.toml', '.js', '.jsx', '.ts', '.tsx', '.ini', '.cfg', '.conf', '.properties')
    $sensitiveAssignments = @{}
    $sensitiveAssignments[("DEEPSEEK_API" + "_KEY")] = "DeepSeek API key"
    $sensitiveAssignments[("SUPABASE_SERVICE" + "_KEY")] = "Supabase service key"
    $sensitiveAssignments[("ANON_SESSION_SIGNING" + "_SECRET")] = "anonymous session signing secret"
    foreach ($name in $sensitiveAssignments.Keys) {
        foreach ($match in (Get-SensitiveAssignments -Content $content -Name $name -AllowColon $allowColonAssignments)) {
            if (Test-TypeScriptTypeMember -Content $content -Match $match -Extension $extension) {
                continue
            }
            $value = $match.Groups["value"].Value
            if (-not (Test-PlaceholderValue $value) -and -not (Test-SafeReference $value)) {
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
