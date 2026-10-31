param(
    [Parameter(Mandatory = $true)]
    [string]$Blender
)

$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repository 'blender\zpm'
$output = Join-Path $repository 'dist'
$tests = Join-Path $repository 'tests\blender_roundtrip.py'
New-Item -ItemType Directory -Path $output -Force | Out-Null

& $Blender --background --factory-startup --python-exit-code 1 --python $tests
if ($LASTEXITCODE -ne 0) { throw 'Blender round-trip tests failed.' }

& $Blender --factory-startup --command extension validate $source
if ($LASTEXITCODE -ne 0) { throw 'Extension validation failed.' }

& $Blender --factory-startup --command extension build --source-dir $source --output-dir $output
if ($LASTEXITCODE -ne 0) { throw 'Extension build failed.' }
