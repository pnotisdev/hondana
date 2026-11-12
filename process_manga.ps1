# Runs mokuro on every volume in the manga folder that has no .html yet.
# Usage:  .\process_manga.ps1            (all unprocessed volumes, including the rest of half-done series)
#         .\process_manga.ps1 "Series name"   (every volume of the named series, even processed ones)
#         .\process_manga.ps1 -MangaRoot "D:\Manga"   (another library)
# The manga folder and the folders to skip come from config.json, like the reader's.
param(
    [string[]]$Series,
    [string]$MangaRoot
)

$configPath = Join-Path $PSScriptRoot "config.json"
$settings = $null
if (Test-Path -LiteralPath $configPath -PathType Leaf) {
    try { $settings = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { Write-Error "Could not read ${configPath}: $_"; exit 1 }
}
if (-not $MangaRoot) {
    $MangaRoot = if ($settings.manga) { $settings.manga } else { "~/Manga" }
    # As in the reader: "~" is the home folder, and relative paths start at the project folder.
    $MangaRoot = $MangaRoot -replace '^~(?=[\\/]|$)', $HOME
    if (-not [IO.Path]::IsPathRooted($MangaRoot)) { $MangaRoot = Join-Path $PSScriptRoot $MangaRoot }
}
$skip = @($settings.skipMangaFolders | Where-Object { $_ })

$root   = $MangaRoot
# "python -m mokuro", not .venv\Scripts\mokuro.exe: that launcher stops working when the folder is moved or renamed
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$mokuro = $null
$mokuroArgs = @()
$venvHasMokuro = $false
if (Test-Path -LiteralPath $python -PathType Leaf) {
    & $python -c "import mokuro" 2>$null
    $venvHasMokuro = $LASTEXITCODE -eq 0
}
if ($venvHasMokuro) {
    $mokuro = $python
    $mokuroArgs = @("-m", "mokuro")
} else {
    $mokuro = (Get-Command mokuro -ErrorAction SilentlyContinue).Source
}
if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    Write-Error "Manga folder not found: $root. Set `"manga`" in config.json or pass -MangaRoot."
    exit 1
}
if (-not $mokuro) {
    Write-Error "Mokuro not found. Install it with: .\Setup.cmd ocr"
    exit 1
}

# -LiteralPath everywhere: folder names like "[Author] Title" are not wildcards
$dirs = Get-ChildItem -LiteralPath $root -Directory | Where-Object { $skip -notcontains $_.Name }
if ($Series) { $dirs = $dirs | Where-Object { $Series -contains $_.Name } }

foreach ($d in $dirs) {
    # a volume is an extracted image folder, or a .cbz that has no folder yet
    $folders = Get-ChildItem -LiteralPath $d.FullName -Directory | Where-Object Name -ne "_ocr"
    $cbz = Get-ChildItem -LiteralPath $d.FullName -Filter *.cbz -File |
           Where-Object { $folders.Name -notcontains $_.BaseName }
    $vols = @($folders.FullName) + @($cbz.FullName) | Where-Object { $_ }
    if (-not $vols) { Write-Host "skip (no volumes): $($d.Name)"; continue }
    # mokuro writes "<volume>.html" next to each volume; the library counts those as ready
    if (-not $Series) {
        $vols = @($vols | Where-Object { -not (Test-Path -LiteralPath (($_ -replace '\.cbz$', '') + '.html')) })
        if (-not $vols) { continue }
    }
    Write-Host "`n=== $($d.Name): $($vols.Count) volumes"
    # "=True" matters: a bare flag would swallow the first path as its value
    & $mokuro @mokuroArgs --disable_confirmation=True @vols
}
