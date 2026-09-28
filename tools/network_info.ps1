# 真手机连接提示。仅使用 .NET 读取本机网卡，避免依赖管理员权限。
function Test-ReaderUsableIPv4 {
    param([string]$Address)
    $taskParsed = $null
    if (![System.Net.IPAddress]::TryParse($Address, [ref]$taskParsed)) { return $false }
    if ($taskParsed.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { return $false }
    if ([System.Net.IPAddress]::IsLoopback($taskParsed)) { return $false }
    return !$Address.StartsWith('169.254.')
}

function Test-ReaderPrivateIPv4 {
    param([string]$Address)
    $taskParts = $Address.Split('.')
    if ($taskParts.Count -ne 4) { return $false }
    $taskFirst = [int]$taskParts[0]
    $taskSecond = [int]$taskParts[1]
    return ($taskFirst -eq 10) -or
        ($taskFirst -eq 192 -and $taskSecond -eq 168) -or
        ($taskFirst -eq 172 -and $taskSecond -ge 16 -and $taskSecond -le 31)
}

function Get-ReaderLanIPv4Addresses {
    $taskDefaultAddress = $null
    $taskSocket = $null
    try {
        # Connect 只选择系统默认路由，不发送数据。
        $taskSocket = New-Object System.Net.Sockets.UdpClient
        $taskSocket.Connect('192.0.2.1', 9)
        $taskDefaultAddress = ([System.Net.IPEndPoint]$taskSocket.Client.LocalEndPoint).Address.IPAddressToString
    } catch {
        $taskDefaultAddress = $null
    } finally {
        if ($null -ne $taskSocket) { $taskSocket.Dispose() }
    }

    $taskRows = @()
    foreach ($taskAdapter in [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) {
        if ($taskAdapter.OperationalStatus -ne [System.Net.NetworkInformation.OperationalStatus]::Up) { continue }
        if ($taskAdapter.NetworkInterfaceType -eq [System.Net.NetworkInformation.NetworkInterfaceType]::Loopback) { continue }
        if ($taskAdapter.NetworkInterfaceType -eq [System.Net.NetworkInformation.NetworkInterfaceType]::Tunnel) { continue }
        foreach ($taskUnicast in $taskAdapter.GetIPProperties().UnicastAddresses) {
            $taskAddress = $taskUnicast.Address.IPAddressToString
            if (!(Test-ReaderUsableIPv4 $taskAddress)) { continue }
            $taskIsVirtual = (($taskAdapter.Name + ' ' + $taskAdapter.Description) -match '(?i)virtual|vmware|hyper-v|vEthernet|wsl|vpn|tap|tailscale|zerotier')
            $taskScore = 0
            if ($taskAddress -eq $taskDefaultAddress) { $taskScore += 200 }
            if (Test-ReaderPrivateIPv4 $taskAddress) { $taskScore += 100 }
            if ($taskAdapter.NetworkInterfaceType -eq [System.Net.NetworkInformation.NetworkInterfaceType]::Wireless80211 -or
                $taskAdapter.NetworkInterfaceType -eq [System.Net.NetworkInformation.NetworkInterfaceType]::Ethernet -or
                $taskAdapter.NetworkInterfaceType -eq [System.Net.NetworkInformation.NetworkInterfaceType]::GigabitEthernet) {
                $taskScore += 20
            }
            if (!$taskIsVirtual) { $taskScore += 500 }
            $taskRows += [pscustomobject]@{
                Address = $taskAddress
                Adapter = $taskAdapter.Name
                IsDefault = ($taskAddress -eq $taskDefaultAddress)
                IsPrivate = (Test-ReaderPrivateIPv4 $taskAddress)
                IsVirtual = $taskIsVirtual
                Score = $taskScore
            }
        }
    }

    $taskSeen = @{}
    foreach ($taskRow in ($taskRows | Sort-Object -Property `
        @{ Expression = { $_.Score }; Descending = $true }, `
        @{ Expression = { $_.Adapter }; Descending = $false }, `
        @{ Expression = { $_.Address }; Descending = $false })) {
        if (!$taskSeen.ContainsKey($taskRow.Address)) {
            $taskSeen[$taskRow.Address] = $true
            Write-Output $taskRow
        }
    }
}

function Show-ReaderPhoneConnectionHelp {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [int]$Port = 8765
    )
    $taskAllAddresses = @(Get-ReaderLanIPv4Addresses)
    $taskAddresses = @($taskAllAddresses | Where-Object { !$_.IsVirtual })
    if ($taskAddresses.Count -eq 0) { $taskAddresses = $taskAllAddresses }
    Write-Host ''
    Write-Host '========== 真手机连接（手机和电脑须连接同一 Wi-Fi） ==========' -ForegroundColor Cyan
    if ($taskAddresses.Count -gt 0) {
        $taskRecommended = $taskAddresses[0]
        Write-Host ('手机“电脑地址”填写： http://{0}:{1}' -f $taskRecommended.Address, $Port) -ForegroundColor Green
        Write-Host ('推荐网卡：{0}' -f $taskRecommended.Adapter)
        if ($taskAddresses.Count -gt 1) {
            Write-Host '若推荐地址不通，再依次试这些地址：'
            foreach ($taskAlternative in $taskAddresses[1..($taskAddresses.Count - 1)]) {
                Write-Host ('  http://{0}:{1}  （{2}）' -f $taskAlternative.Address, $Port, $taskAlternative.Adapter)
            }
        }
    } else {
        Write-Host '没有找到可用的 IPv4 地址。请先让电脑连接 Wi-Fi 或网线，再重新运行本 BAT。' -ForegroundColor Yellow
    }
    Write-Host ('安装包：{0}' -f (Join-Path $ProjectRoot 'reader.apk'))
    Write-Host '配对口令：没改过就是 reader-local；改过则看 config\reader.json 的 token。'
    Write-Host '步骤：1. 手机和电脑连同一 Wi-Fi；2. 安装 APK；3. 填上面的完整地址和口令；4. 等待显示“电脑已连接”；5. 按提示开启浮窗、整个屏幕投屏；自动翻页还需开启无障碍。'
    Write-Host '注意：真手机不要填 127.0.0.1 或 10.0.2.2；10.0.2.2 只供安卓模拟器使用。' -ForegroundColor Yellow
    Write-Host '首次出现 Windows 防火墙询问时，只勾“专用网络”并允许访问。'
    Write-Host '================================================================'
    Write-Host ''
}
