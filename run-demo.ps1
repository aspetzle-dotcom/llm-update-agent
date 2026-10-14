$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv run llm-updates init --config demo.toml --demo
        if ($LASTEXITCODE -ne 0) { throw 'Demo initialization failed.' }
        uv run llm-updates --config demo.toml run --fixture fixtures/demo.jsonl --since 2026-10-07 --until 2026-10-14
    } else {
        python -m llm_updates init --config demo.toml --demo
        if ($LASTEXITCODE -ne 0) { throw 'Install the project using the README before running this script.' }
        python -m llm_updates --config demo.toml run --fixture fixtures/demo.jsonl --since 2026-10-07 --until 2026-10-14
    }
    if ($LASTEXITCODE -ne 0) { throw 'Demo run did not finish successfully.' }
} finally {
    Pop-Location
}
