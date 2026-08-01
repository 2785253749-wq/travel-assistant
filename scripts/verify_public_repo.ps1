$ErrorActionPreference = "Stop"

function Test-PlaceholderValue {
    param([string]$Value)

    $normalized = $Value.Trim().Trim('"').Trim("'")
    if ([string]::IsNullOrWhiteSpace($normalized)) {
        return $true
    }

    return $normalized -match '^(your_|replace_|replace-|example|placeholder|test-only-key$|test-key$)'
}

$trackedFiles = @(& git ls-files)
if ($LASTEXITCODE -ne 0) {
    Write-Output "Public repository check failed: git ls-files could not be read."
    exit 2
}

$violations = [System.Collections.Generic.List[string]]::new()

foreach ($file in $trackedFiles) {
    $normalizedPath = $file.Replace('\', '/')
    $forbiddenPath =
        (($normalizedPath -match '(^|/)\.env($|\.)') -and ($normalizedPath -notmatch '(^|/)\.env\.example$')) -or
        ($normalizedPath -match '(^|/)\.(venv|pytest_cache|agents)(/|$)') -or
        ($normalizedPath -match '(^|/)__pycache__(/|$)') -or
        ($normalizedPath -match '\.(db|sqlite|sqlite3|log)$')

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

    $deepSeekAssignments = [regex]::Matches(
        $content,
        '^\s*DEEPSEEK_API_KEY\s*=\s*([^\s#]+)',
        [System.Text.RegularExpressions.RegexOptions]::Multiline
    )
    foreach ($match in $deepSeekAssignments) {
        if (-not (Test-PlaceholderValue $match.Groups[1].Value)) {
            $violations.Add("Credential pattern (DeepSeek API key): $normalizedPath")
            break
        }
    }

    $supabaseAssignments = [regex]::Matches(
        $content,
        '^\s*SUPABASE_SERVICE_KEY\s*=\s*([^\s#]+)',
        [System.Text.RegularExpressions.RegexOptions]::Multiline
    )
    foreach ($match in $supabaseAssignments) {
        if (-not (Test-PlaceholderValue $match.Groups[1].Value)) {
            $violations.Add("Credential pattern (Supabase service key): $normalizedPath")
            break
        }
    }

    if ($content -match '(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{20,}' -or
        $content -match '(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{20,}') {
        $violations.Add("Credential pattern (GitHub token): $normalizedPath")
    }

    if ($content -match '-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----') {
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
