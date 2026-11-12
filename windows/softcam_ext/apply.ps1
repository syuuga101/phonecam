# softcam (PhoneCam 改名パッチ適用後) に、カメラ設定ダイアログ用の拡張を入れる。
#   - PhoneCamControls.h を src/softcamcore にコピー (ヘッダのみ。vcxproj の変更は不要)
#   - Softcam フィルタに IAMVideoProcAmp / IAMCameraControl / ISpecifyPropertyPages を追加
# 使い方: ./apply.ps1 -SoftcamDir softcam-src
# アンカーが見つからない / 複数ある場合は例外で止まる (黙って壊れたビルドを作らない)。
param(
    [Parameter(Mandatory = $true)][string]$SoftcamDir
)
$ErrorActionPreference = "Stop"

$core = Join-Path (Resolve-Path $SoftcamDir).Path "src/softcamcore"
if (-not (Test-Path $core)) { throw "softcamcore フォルダがありません: $core" }

Copy-Item (Join-Path $PSScriptRoot "PhoneCamControls.h") (Join-Path $core "PhoneCamControls.h") -Force

function Patch-Once([string]$path, [string]$anchor, [string]$replacement) {
    $text = [System.IO.File]::ReadAllText($path)
    $first = $text.IndexOf($anchor, [System.StringComparison]::Ordinal)
    if ($first -lt 0) { throw "アンカーが見つかりません: '$anchor' in $path" }
    $second = $text.IndexOf($anchor, $first + 1, [System.StringComparison]::Ordinal)
    if ($second -ge 0) { throw "アンカーが複数あります: '$anchor' in $path" }
    $text = $text.Substring(0, $first) + $replacement + $text.Substring($first + $anchor.Length)
    [System.IO.File]::WriteAllText($path, $text)
}

$h   = Join-Path $core "DShowSoftcam.h"
$cpp = Join-Path $core "DShowSoftcam.cpp"

# ---- DShowSoftcam.h ----
Patch-Once $h '#include "FrameBuffer.h"' ('#include "FrameBuffer.h"' + "`n" + '#include "PhoneCamControls.h"')

Patch-Once $h 'class Softcam : public CSource, public IAMStreamConfig' `
              'class Softcam : public CSource, public IAMStreamConfig, public ISpecifyPropertyPages'

Patch-Once $h '    FrameBuffer*    getFrameBuffer();' (
    '    // ISpecifyPropertyPages (PhoneCam: OBS "Configure Video" opens the system camera pages)' + "`n" +
    '    STDMETHODIMP GetPages(CAUUID *pPages) override { return phonecam::FillPropertyPages(pPages); }' + "`n" + "`n" +
    '    FrameBuffer*    getFrameBuffer();')

Patch-Once $h '    const float m_framerate;' ('    const float m_framerate;' + "`n" + '    phonecam::CameraControls m_ctl;')

# ---- DShowSoftcam.cpp ----
Patch-Once $cpp '    m_framerate(m_frame_buffer.framerate())' ('    m_framerate(m_frame_buffer.framerate()),' + "`n" + '    m_ctl(GetOwner())')

Patch-Once $cpp '    if (riid == IID_IAMStreamConfig)' (
    '    if (IsEqualGUID(riid, __uuidof(IAMVideoProcAmp)))' + "`n" +
    '    {' + "`n" +
    '        return GetInterface(m_ctl.procAmp(), ppv);' + "`n" +
    '    }' + "`n" +
    '    if (IsEqualGUID(riid, __uuidof(IAMCameraControl)))' + "`n" +
    '    {' + "`n" +
    '        return GetInterface(m_ctl.cameraControl(), ppv);' + "`n" +
    '    }' + "`n" +
    '    if (IsEqualGUID(riid, __uuidof(ISpecifyPropertyPages)))' + "`n" +
    '    {' + "`n" +
    '        return GetInterface(static_cast<ISpecifyPropertyPages*>(this), ppv);' + "`n" +
    '    }' + "`n" +
    '    if (riid == IID_IAMStreamConfig)')

# ---- 検証 ----
$ht = [System.IO.File]::ReadAllText($h)
$ct = [System.IO.File]::ReadAllText($cpp)
foreach ($needle in @('PhoneCamControls.h', 'ISpecifyPropertyPages', 'phonecam::CameraControls m_ctl;')) {
    if (-not $ht.Contains($needle)) { throw "検証に失敗しました (DShowSoftcam.h): $needle" }
}
foreach ($needle in @('m_ctl(GetOwner())', 'm_ctl.procAmp()', 'm_ctl.cameraControl()')) {
    if (-not $ct.Contains($needle)) { throw "検証に失敗しました (DShowSoftcam.cpp): $needle" }
}
if (-not (Test-Path (Join-Path $core "PhoneCamControls.h"))) { throw "PhoneCamControls.h がコピーされていません" }
Write-Host "softcam にカメラ設定の拡張を適用しました"
