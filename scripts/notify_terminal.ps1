param(
    [Parameter(Mandatory = $true)]
    [string]$Title,

    [Parameter(Mandatory = $true)]
    [string]$Body,

    [Parameter(Mandatory = $true)]
    [string]$PacketPath,

    [switch]$Probe
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $PacketPath -PathType Leaf)) {
    throw "Terminal packet does not exist: $PacketPath"
}

$runningOnWindows = (
    $PSVersionTable.PSEdition -eq 'Desktop' -or
    [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
)

if (-not $runningOnWindows) {
    throw 'Windows toast notifications are unavailable on this host.'
}

[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

if ($Probe) {
    [pscustomobject]@{
        supported = $true
        winrt_loaded = $true
        packet = (Resolve-Path -LiteralPath $PacketPath).Path
        silent = $true
    } | ConvertTo-Json -Compress
    exit 0
}

$escapedTitle = [System.Security.SecurityElement]::Escape($Title)
$escapedBody = [System.Security.SecurityElement]::Escape($Body)
$toastXml = @"
<toast duration="short">
  <visual>
    <binding template="ToastGeneric">
      <text>$escapedTitle</text>
      <text>$escapedBody</text>
    </binding>
  </visual>
  <audio silent="true"/>
</toast>
"@

$xmlDocument = New-Object Windows.Data.Xml.Dom.XmlDocument
$xmlDocument.LoadXml($toastXml)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xmlDocument)
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('OpenAI.Codex')
$notifier.Show($toast)

[pscustomobject]@{
    notified = $true
    silent = $true
    packet = (Resolve-Path -LiteralPath $PacketPath).Path
} | ConvertTo-Json -Compress
