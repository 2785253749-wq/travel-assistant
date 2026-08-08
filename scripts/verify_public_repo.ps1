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

function Test-JavaScriptSafeReference {
    param([string]$Value)

    $normalized = $Value.Trim().Trim('"').Trim("'").Trim()
    return (
        $normalized -match '^(?i:(?:process|import\.meta)\.env\.[A-Z][A-Z0-9_]*)$' -or
        $normalized -match '^(?i:(?:process|import\.meta)\.env\[(?:"[A-Z][A-Z0-9_]*"|''[A-Z][A-Z0-9_]*'')\])$'
    )
}

function Test-SafeReference {
    param([string]$Value)

    $normalized = $Value.Trim().Trim('"').Trim("'").Trim()
    return (
        $normalized -match '^\$[A-Z][A-Z0-9_]*$' -or
        $normalized -match '^\$\{[A-Z][A-Z0-9_]*\}$' -or
        $normalized -match '^%[A-Z][A-Z0-9_]*%$' -or
        (Test-JavaScriptSafeReference -Value $normalized) -or
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
    $pattern = "(?im)(?:^|[,{;])\s*(?:(?:export|const|let|var)\s+)?(?<key>$receiver[`"']?$escapedName[`"']?)\??\s*(?<separator>$separator)\s*"
    return [regex]::Matches($Content, $pattern)
}

function Get-AssignedExpression {
    param(
        [string]$Content,
        [int]$StartIndex
    )

    $builder = [System.Text.StringBuilder]::new()
    $quote = $null
    $braceDepth = 0
    for ($index = $StartIndex; $index -lt $Content.Length; $index++) {
        $character = $Content[$index]
        if ($null -ne $quote) {
            [void]$builder.Append($character)
            if ($character -eq [char]92) {
                if ($index + 1 -lt $Content.Length) {
                    $index++
                    [void]$builder.Append($Content[$index])
                }
            }
            elseif ($character -eq $quote) {
                $quote = $null
            }
            continue
        }

        if ($character -in @([char]34, [char]39, [char]96)) {
            $quote = $character
            [void]$builder.Append($character)
            continue
        }
        if ($character -in @([char]10, [char]13) -or
            ($character -eq [char]47 -and $index + 1 -lt $Content.Length -and $Content[$index + 1] -in @([char]47, [char]42))) {
            $useJavaScriptContinuations = Test-JavaScriptSafeReference -Value $builder.ToString()
            $continuation = Get-ExpressionContinuationStart `
                -Content $Content `
                -StartIndex $index `
                -UseJavaScriptContinuations $useJavaScriptContinuations
            if ($continuation -lt 0) {
                break
            }
            [void]$builder.Append(' ')
            $index = $continuation - 1
            continue
        }
        if ($character -eq [char]35) {
            break
        }
        if ($character -eq [char]123) {
            $braceDepth++
            [void]$builder.Append($character)
            continue
        }
        if ($character -eq [char]125) {
            if ($braceDepth -eq 0) {
                break
            }
            $braceDepth--
            [void]$builder.Append($character)
            continue
        }
        if ($braceDepth -eq 0 -and $character -in @([char]44, [char]59)) {
            break
        }
        [void]$builder.Append($character)
    }
    return $builder.ToString().Trim()
}

function Get-ExpressionContinuationStart {
    param(
        [string]$Content,
        [int]$StartIndex,
        [bool]$UseJavaScriptContinuations
    )

    $index = $StartIndex
    while ($index -lt $Content.Length) {
        if ($Content[$index] -in @([char]9, [char]10, [char]13, [char]32)) {
            $index++
            continue
        }
        if ($Content[$index] -eq [char]47 -and $index + 1 -lt $Content.Length) {
            if ($Content[$index + 1] -eq [char]47) {
                $index += 2
                while ($index -lt $Content.Length -and $Content[$index] -notin @([char]10, [char]13)) {
                    $index++
                }
                continue
            }
            if ($Content[$index + 1] -eq [char]42) {
                $commentEnd = $Content.IndexOf('*/', $index + 2, [System.StringComparison]::Ordinal)
                if ($commentEnd -lt 0) {
                    return -1
                }
                $index = $commentEnd + 2
                continue
            }
        }
        if ($index + 1 -lt $Content.Length -and
            (($Content[$index] -eq [char]124 -and $Content[$index + 1] -eq [char]124) -or
             ($Content[$index] -eq [char]38 -and $Content[$index + 1] -eq [char]38) -or
             ($Content[$index] -eq [char]63 -and $Content[$index + 1] -eq [char]63))) {
            return $index
        }
        if (-not $UseJavaScriptContinuations) {
            return -1
        }
        $continuationPunctuators = @(
            [char]37, [char]38, [char]40, [char]42, [char]43, [char]45,
            [char]46, [char]47, [char]60, [char]61, [char]62, [char]63,
            [char]91, [char]94, [char]96, [char]124
        )
        if ($Content[$index] -in $continuationPunctuators) {
            return $index
        }
        if ($Content[$index] -eq [char]33 -and
            $index + 1 -lt $Content.Length -and
            $Content[$index + 1] -eq [char]61) {
            return $index
        }
        $remaining = $Content.Substring($index)
        if ($remaining -match '^(?i:(?:in|instanceof|as|satisfies))\b') {
            return $index
        }
        return -1
    }
    return -1
}

function Get-MatchingBraceIndex {
    param(
        [string]$Content,
        [int]$OpenIndex
    )

    $depth = 0
    $quote = $null
    $inLineComment = $false
    $inBlockComment = $false
    for ($index = $OpenIndex; $index -lt $Content.Length; $index++) {
        $character = $Content[$index]
        $next = if ($index + 1 -lt $Content.Length) { $Content[$index + 1] } else { $null }

        if ($inLineComment) {
            if ($character -eq [char]10) {
                $inLineComment = $false
            }
            continue
        }
        if ($inBlockComment) {
            if ($character -eq [char]42 -and $next -eq [char]47) {
                $inBlockComment = $false
                $index++
            }
            continue
        }
        if ($null -ne $quote) {
            if ($character -eq [char]92) {
                $index++
            }
            elseif ($character -eq $quote) {
                $quote = $null
            }
            continue
        }

        if ($character -eq [char]47 -and $next -eq [char]47) {
            $inLineComment = $true
            $index++
            continue
        }
        if ($character -eq [char]47 -and $next -eq [char]42) {
            $inBlockComment = $true
            $index++
            continue
        }
        if ($character -in @([char]34, [char]39, [char]96)) {
            $quote = $character
            continue
        }
        if ($character -eq [char]123) {
            $depth++
            continue
        }
        if ($character -eq [char]125) {
            $depth--
            if ($depth -eq 0) {
                return $index
            }
        }
    }
    return -1
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
    $declarationPattern = '(?im)\b(?:interface\s+[A-Za-z_$][A-Za-z0-9_$]*(?:<[^>{}]+>)?(?:\s+extends[^\{]+)?|type\s+[A-Za-z_$][A-Za-z0-9_$]*(?:<[^>{}]+>)?\s*=)\s*\{'
    foreach ($declaration in [regex]::Matches($Content, $declarationPattern)) {
        $openIndex = $declaration.Index + $declaration.Length - 1
        if ($openIndex -ge $keyIndex) {
            continue
        }
        $closeIndex = Get-MatchingBraceIndex -Content $Content -OpenIndex $openIndex
        if ($closeIndex -ge $keyIndex) {
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
            $valueStart = $match.Index + $match.Length
            $value = Get-AssignedExpression -Content $content -StartIndex $valueStart
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
