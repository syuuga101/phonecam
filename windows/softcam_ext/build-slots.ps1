param(
    [Parameter(Mandatory)] [string] $SoftcamDir,
    [Parameter(Mandatory)] [string] $OutputDir,
    [ValidateSet("x64", "Win32")] [string] $Platform = "x64",
    [string] $MSBuild = "msbuild",
    [string] $Toolset
)
$ErrorActionPreference = "Stop"
$source = (Resolve-Path -LiteralPath $SoftcamDir).Path
$output = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDir)
New-Item -ItemType Directory -Force -Path $output | Out-Null
$paths = @("src/softcam/softcam.cpp", "src/softcamcore/DShowSoftcam.cpp", "src/softcamcore/FrameBuffer.cpp")
$headerSource = "src/softcamcore/DShowSoftcam.h"
$original = @{$headerSource = [IO.File]::ReadAllText((Join-Path $source $headerSource))}
foreach ($path in $paths) {
    $original[$path] = [IO.File]::ReadAllText((Join-Path $source $path))
    if (-not $original[$path].Contains("DirectShow Softcam")) { throw "Unexpected softcam source: $path" }
}
$header = Join-Path $source "src/softcamcore/PhoneCamControls.h"
Push-Location $source
try {
    foreach ($slot in 1..4) {
        $suffix = if ($slot -eq 1) { "" } else { [string]$slot }
        $display = if ($slot -eq 1) { "PhoneCam" } else { "PhoneCam $slot" }
        $last = "{0:x2}" -f (0x43 + $slot)
        foreach ($path in $paths) {
            $name = if ($path -like "*FrameBuffer.cpp") { "PhoneCam$suffix" } else { $display }
            $text = $original[$path].Replace("DirectShow Softcam", $name)
            if ($path -like "*FrameBuffer.cpp") {
                # Byte 15 is reserved upstream. Increment it only while a consumer pulls frames.
                $anchor = "    auto frame = header();"
                $text = $text.Replace("        frame->m_connected = 0;", "        frame->m_connected = 0;`n        frame->m_unused_field = 0;")
                $start = $text.IndexOf("void FrameBuffer::transferToDIB(")
                $pos = $text.IndexOf($anchor, $start)
                if ($pos -lt 0) { throw "Receiver heartbeat patch anchor missing" }
                $text = $text.Insert($pos + $anchor.Length, "`n    ++frame->m_unused_field;")
            }
            if ($path -eq $paths[0]) {
                $text = $text.Replace("AEF3B972-5FA5-4647-9571-358EB472BC9E", "BD7AEDAC-501F-4685-9B20-BC10F70BD1$last")
                $text = $text.Replace("0xaef3b972, 0x5fa5, 0x4647, 0x95, 0x71, 0x35, 0x8e, 0xb4, 0x72, 0xbc, 0x9e",
                                      "0xbd7aedac, 0x501f, 0x4685, 0x9b, 0x20, 0xbc, 0x10, 0xf7, 0x0b, 0xd1, 0x$last")
                if ($text -notmatch "(?i)0xbd7aedac" -or $text -match "(?i)aef3b972") { throw "CLSID patch failed" }
            }
            [IO.File]::WriteAllText((Join-Path $source $path), $text)
        }
        [IO.File]::WriteAllText((Join-Path $source $headerSource), $original[$headerSource])
        & (Join-Path $PSScriptRoot "apply.ps1") -SoftcamDir $source
        $text = [IO.File]::ReadAllText($header).Replace("PhoneCam/Controls", "PhoneCam$suffix/Controls")
        [IO.File]::WriteAllText($header, $text)
        $args = @("softcam.sln", "-t:softcam", "-p:Configuration=Release", "-p:Platform=$Platform", "-m", "-v:minimal")
        if ($Toolset) { $args += "-p:PlatformToolset=$Toolset" }
        & $MSBuild @args
        if ($LASTEXITCODE -ne 0) { throw "softcam $Platform slot $slot build failed" }
        $dll = "dist/bin/$Platform/softcam.dll"
        if (-not (Test-Path -LiteralPath $dll)) { throw "Missing $dll" }
        $slotDir = if ($slot -eq 1) { $output } else { Join-Path $output $slot }
        New-Item -ItemType Directory -Force -Path $slotDir | Out-Null
        $file = if ($Platform -eq "Win32") { "softcam32.dll" } else { "softcam.dll" }
        Copy-Item -LiteralPath $dll -Destination (Join-Path $slotDir $file) -Force
    }
} finally {
    foreach ($path in $original.Keys) { [IO.File]::WriteAllText((Join-Path $source $path), $original[$path]) }
    Pop-Location
}
