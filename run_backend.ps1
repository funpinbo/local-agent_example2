# remote 모드 백엔드: 그룹웨어 + msdf up + 서비스(docs, gw). 각각 새 창에서 뜬다.
#   .\run_backend.ps1
# 그 뒤 에이전트를 MSDF_MODE=remote로 실행한다.  UI: http://127.0.0.1:8787/ui
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$msdf = Join-Path $root ".venv\Scripts\msdf.exe"
$env:PYTHONPATH = $root

Start-Process -FilePath $py -ArgumentList "-m","uvicorn","infra.groupware.app:app","--port","8100" -WorkingDirectory $root
Start-Process -FilePath $msdf -ArgumentList "up","--root","infra\msdf" -WorkingDirectory $root
Start-Sleep -Seconds 3
Start-Process -FilePath $py -ArgumentList "infra\msdf\services\docs.py" -WorkingDirectory $root
Start-Process -FilePath $py -ArgumentList "infra\msdf\services\groupware_sync.py" -WorkingDirectory $root
Write-Host "그룹웨어 http://127.0.0.1:8100  /  MSDF UI http://127.0.0.1:8787/ui"
