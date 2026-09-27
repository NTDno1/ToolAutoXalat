param(
    [string]$WebhookUrl = 'http://localhost:5678/webhook-test/95e98a36-03ad-4b9e-b074-da9430455b05'
)

$ErrorActionPreference = 'Continue'
$now = (Get-Date).ToUniversalTime().ToString('o')

function New-Envelope(
    [string]$NotificationId,
    [string]$Type,
    [string]$EventCode,
    [string]$Severity,
    [string]$Title,
    [string]$Message,
    [hashtable]$Data
) {
    [ordered]@{
        schemaVersion = '1.0'
        source = 'ToolAutoXalat'
        notificationId = $NotificationId
        type = $Type
        eventCode = $EventCode
        severity = $Severity
        title = $Title
        message = $Message
        occurredAtUtc = $script:now
        data = $Data
    }
}

$payloads = @(
    @{
        name = 'STREAK_ALERT_MEAT_3'
        body = New-Envelope `
            -NotificationId 'test:MEAT_STREAK_3:round-9001' `
            -Type 'STREAK_ALERT' `
            -EventCode 'MEAT_STREAK_3' `
            -Severity 'WARNING' `
            -Title 'TEST - Bet Thit dat 3 cau' `
            -Message 'TEST: Da xuat hien 3 ket qua Thit lien tuc.' `
            -Data @{
                deliveryMode = 'BROADCAST'
                recipientCount = 0
                alert = @{
                    alertKey = 'test:MEAT_STREAK_3:round-9001'
                    ruleCode = 'MEAT_STREAK_3'
                    category = 'MEAT'
                    streakLength = 3
                    threshold = 3
                    resultId = 9001
                    roundNumber = 901
                    itemCode = 'BO'
                    itemName = 'Bo'
                    detectedAtUtc = $now
                    localDate = '2026-09-14'
                }
                recipients = @()
                test = $true
            }
    },
    @{
        name = 'STREAK_ALERT_VEGETABLE_10'
        body = New-Envelope `
            -NotificationId 'test:VEGETABLE_STREAK_10:round-9010' `
            -Type 'STREAK_ALERT' `
            -EventCode 'VEGETABLE_STREAK_10' `
            -Severity 'WARNING' `
            -Title 'TEST - Bet Rau dat 10 cau' `
            -Message 'TEST: Da xuat hien 10 ket qua Rau lien tuc.' `
            -Data @{
                deliveryMode = 'BROADCAST'
                recipientCount = 0
                alert = @{
                    alertKey = 'test:VEGETABLE_STREAK_10:round-9010'
                    ruleCode = 'VEGETABLE_STREAK_10'
                    category = 'VEGETABLE'
                    streakLength = 10
                    threshold = 10
                    resultId = 9010
                    roundNumber = 910
                    itemCode = 'CAI'
                    itemName = 'Cai'
                    detectedAtUtc = $now
                    localDate = '2026-09-14'
                }
                recipients = @()
                test = $true
            }
    },
    @{
        name = 'PHONE_CONNECTION_FAILED'
        body = New-Envelope `
            -NotificationId 'test:system-event:PHONE_CONNECTION_FAILED' `
            -Type 'SCANNER_SYSTEM_EVENT' `
            -EventCode 'PHONE_CONNECTION_FAILED' `
            -Severity 'ERROR' `
            -Title 'TEST - Loi ket noi dien thoai' `
            -Message 'TEST: Khong the ket noi lai dien thoai sau 5 lan thu.' `
            -Data @{ eventId = 91001; details = @{ sourceSerial = 'c6ac0650'; failedAttempts = 5; reconnectIntervalSeconds = 5 }; test = $true }
    },
    @{
        name = 'SCANNER_HEARTBEAT_STALLED'
        body = New-Envelope `
            -NotificationId 'test:system-event:SCANNER_HEARTBEAT_STALLED' `
            -Type 'SCANNER_SYSTEM_EVENT' `
            -EventCode 'SCANNER_HEARTBEAT_STALLED' `
            -Severity 'CRITICAL' `
            -Title 'TEST - Scanner treo heartbeat' `
            -Message 'TEST: Scanner bi treo hoac da dung cap nhat heartbeat.' `
            -Data @{ eventId = 91002; details = @{ sourceSerial = 'c6ac0650'; staleAfterSeconds = 60 }; test = $true }
    },
    @{
        name = 'SCANNER_RESULT_STALLED'
        body = New-Envelope `
            -NotificationId 'test:system-event:SCANNER_RESULT_STALLED' `
            -Type 'SCANNER_SYSTEM_EVENT' `
            -EventCode 'SCANNER_RESULT_STALLED' `
            -Severity 'ERROR' `
            -Title 'TEST - Scanner khong ghi nhan cau tiep theo' `
            -Message 'TEST: Scanner van online nhung khong ghi nhan duoc cac cau tiep theo.' `
            -Data @{ eventId = 91003; details = @{ sourceSerial = 'c6ac0650'; currentRound = 141; resultStaleAfterSeconds = 90 }; test = $true }
    },
    @{
        name = 'DETECTION_FAILED'
        body = New-Envelope `
            -NotificationId 'test:system-event:DETECTION_FAILED' `
            -Type 'SCANNER_SYSTEM_EVENT' `
            -EventCode 'DETECTION_FAILED' `
            -Severity 'ERROR' `
            -Title 'TEST - Khong nhan dien du 8 o' `
            -Message 'TEST: Khong nhan dien du 8 o sau 4 lan quet.' `
            -Data @{ eventId = 91004; details = @{ sourceSerial = 'c6ac0650'; failedAttempts = 4; expectedSlots = 8 }; test = $true }
    },
    @{
        name = 'BETTING_SIGNAL_HOT_AND_COINS'
        body = New-Envelope `
            -NotificationId 'test:betting-signal:round-911' `
            -Type 'BETTING_SIGNAL' `
            -EventCode 'HOT_AND_COINS' `
            -Severity 'INFO' `
            -Title 'TEST - Hot va luong xu cuoc' `
            -Message 'TEST: Da phat hien cua Hot va muc xu trong thoi gian dat cau.' `
            -Data @{
                round = 911
                countdownSeconds = 18
                hotItemCode = 'CA_ROT'
                items = @(
                    @{ itemCode = 'BANH_MI'; coinCount = 1; activityPercent = 33 },
                    @{ itemCode = 'CA_CHUA'; coinCount = 0; activityPercent = 0 },
                    @{ itemCode = 'XIEN'; coinCount = 0; activityPercent = 0 },
                    @{ itemCode = 'CAI'; coinCount = 0; activityPercent = 0 },
                    @{ itemCode = 'DUI'; coinCount = 1; activityPercent = 33 },
                    @{ itemCode = 'NGO'; coinCount = 0; activityPercent = 0 },
                    @{ itemCode = 'BO'; coinCount = 0; activityPercent = 0 },
                    @{ itemCode = 'CA_ROT'; coinCount = 2; activityPercent = 67 }
                )
                test = $true
            }
    }
)

$results = foreach ($payload in $payloads) {
    $json = $payload.body | ConvertTo-Json -Depth 20
    try {
        $response = Invoke-WebRequest `
            -Uri $WebhookUrl `
            -Method Post `
            -Body $json `
            -ContentType 'application/json; charset=utf-8' `
            -TimeoutSec 10 `
            -UseBasicParsing
        [pscustomobject]@{
            Case = $payload.name
            StatusCode = [int]$response.StatusCode
            Ok = $true
            Response = $response.Content
        }
    } catch {
        $statusCode = $null
        if ($_.Exception.Response) { $statusCode = [int]$_.Exception.Response.StatusCode }
        [pscustomobject]@{
            Case = $payload.name
            StatusCode = $statusCode
            Ok = $false
            Response = $_.Exception.Message
        }
    }
    Start-Sleep -Milliseconds 250
}

$results | Format-Table -AutoSize
