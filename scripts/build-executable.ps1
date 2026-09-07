[CmdletBinding()]
param(
    [string]$PythonExecutable = "",
    [switch]$SkipToolInstall,
    [switch]$Console
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Detect the OS via .NET reflection rather than $IsWindows/$IsMacOS/$IsLinux:
# those automatic variables only exist under PowerShell 7+ (pwsh), and under
# Set-StrictMode -Version Latest, referencing an undefined variable throws --
# this keeps the script working under both pwsh (macOS/Linux/Windows CI) and
# legacy Windows PowerShell 5.1.
$runtime_platform = [System.Runtime.InteropServices.RuntimeInformation]
$is_windows_platform = $runtime_platform::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::Windows)
$is_macos_platform = $runtime_platform::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::OSX)

$pyinstaller_version = "6.21.0"
$script_directory = Split-Path -Parent $PSCommandPath
$repository_root = (Resolve-Path -LiteralPath (Join-Path $script_directory "..")).Path
$spec_path = Join-Path $repository_root "pyinstaller.spec"
$python_wrapper = Join-Path $script_directory "invoke-build-python.py"
$dist_directory = Join-Path $repository_root "dist"
$work_directory = Join-Path $repository_root "build" "pyinstaller"
$tool_directory = Join-Path $repository_root "build" "pyinstaller-tools"
# pyinstaller.spec produces despatch.exe on Windows, a Despatch.app bundle on
# macOS, and a plain despatch binary on Linux.
$executable_name = if ($is_windows_platform) {
    "despatch.exe"
} elseif ($is_macos_platform) {
    "Despatch.app"
} else {
    "despatch"
}
$executable_path = Join-Path $dist_directory $executable_name
$original_python_path = $env:PYTHONPATH
$original_console_build = $env:DESPATCH_CONSOLE_BUILD

function Invoke-BuildPython {
    param(
        [Parameter(Mandatory)]
        [string[]]$PythonArguments
    )

    $status_path = [IO.Path]::GetTempFileName()
    Remove-Item -LiteralPath $status_path -Force
    try {
        $wrapper_arguments = @(
            $python_wrapper,
            "--status",
            $status_path,
            "--tool-directory",
            $tool_directory,
            "--"
        ) + $PythonArguments
        if ($PythonExecutable) {
            & $PythonExecutable @wrapper_arguments
        } else {
            & envoy python @wrapper_arguments
        }
        if (-not (Test-Path -LiteralPath $status_path)) {
            throw "Build Python did not report an exit code."
        }
        $status = Get-Content -LiteralPath $status_path -Raw | ConvertFrom-Json
        if ($status.exitCode -ne 0) {
            throw "Python command failed with exit code $($status.exitCode)."
        }
    } finally {
        Remove-Item -LiteralPath $status_path -Force -ErrorAction SilentlyContinue
    }
}

function Test-BuildTools {
    try {
        Invoke-BuildPython @(
            "-c",
            "import PyInstaller, material, properdocs, sys; " +
                "sys.exit(PyInstaller.__version__ != '$pyinstaller_version')"
        ) *> $null
        return $true
    } catch {
        return $false
    }
}

try {
    $env:DESPATCH_CONSOLE_BUILD = if ($Console) { "1" } else { "0" }
    if ($PythonExecutable) {
        $resolved_python = Get-Command -Name $PythonExecutable -ErrorAction Stop
        $PythonExecutable = $resolved_python.Source
    } else {
        Get-Command -Name "envoy" -ErrorAction Stop | Out-Null
    }

    if (-not (Test-BuildTools)) {
        if ($SkipToolInstall) {
            throw "PyInstaller $pyinstaller_version and the documentation tools are required."
        }
        New-Item -ItemType Directory -Path $tool_directory -Force | Out-Null
        Invoke-BuildPython @(
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--upgrade",
            "--target",
            $tool_directory,
            "PyInstaller==$pyinstaller_version",
            "properdocs",
            "mkdocs-material"
        )
        if (-not (Test-BuildTools)) {
            throw "The required build tools are unavailable after installation."
        }
    }

    Invoke-BuildPython @(
        "-c",
        "import PyInstaller, PySide6, Qt, envoy, despatch, properdocs; " +
            "assert hasattr(envoy, 'getConfigRoot'), " +
            "'Envoy must expose getConfigRoot()'; " +
            "print('Freezing with', PyInstaller.__version__, Qt.__binding__)"
    )

    New-Item -ItemType Directory -Path $dist_directory -Force | Out-Null
    New-Item -ItemType Directory -Path $work_directory -Force | Out-Null

    Push-Location $repository_root
    try {
        Invoke-BuildPython @(
            "-m",
            "properdocs",
            "build",
            "--clean"
        )
        Invoke-BuildPython @(
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--distpath",
            $dist_directory,
            "--workpath",
            $work_directory,
            $spec_path
        )
    } finally {
        Pop-Location
    }

    # Despatch.app on macOS is a bundle *directory*, not a single file --
    # Test-Path/Get-Item must branch on Container vs Leaf accordingly, or
    # this check (and the byte-size report below) always fails there.
    $expected_path_type = if ($is_macos_platform) { "Container" } else { "Leaf" }
    if (-not (Test-Path -LiteralPath $executable_path -PathType $expected_path_type)) {
        throw "PyInstaller completed without creating '$executable_path'."
    }
    $built_executable = Get-Item -LiteralPath $executable_path
    if ($is_macos_platform) {
        Write-Host "Built $($built_executable.FullName)"
    } else {
        Write-Host "Built $($built_executable.FullName) ($($built_executable.Length) bytes)"
    }
} finally {
    $env:PYTHONPATH = $original_python_path
    $env:DESPATCH_CONSOLE_BUILD = $original_console_build
}
