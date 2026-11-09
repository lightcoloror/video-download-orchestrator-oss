# MediaGo client launcher source

`scripts/start-mediago-client.ps1` is the existing first-party operator launcher,
adapted to accept an explicit client file path and honor ordinary host policy.
Original source SHA-256: `821803e08606e013a89671a704b1d84a6db3c6d58fec0884384d347027778a2c`.
The first-party wrapper is Apache-2.0. No MediaGo executable, assets, SDK or
third-party redistribution rights are included.

Place it beside the existing `scripts/start-ui.ps1` and `runtime-python.ps1`.
When an operator chooses to start the backend and interactive desktop client:

```powershell
powershell.exe -NoProfile -File .\scripts\start-mediago-client.ps1 -MediaGoPath "C:\path\to\mediago-community.exe"
```

This command has process/service effects; it is not a dry-run check. Blank or
missing client paths are rejected. The launcher checks the explicit file and
resolves its literal path before comparing process paths or launching it.
It calls the existing backend entry with `-NoBrowser`, checks the native exit
code before any client action, and reuses the first matching running client.
There is no automatic client search, policy bypass, elevation or host fallback.
The printed `http://127.0.0.1:8921/` is the existing default display URL,
not evidence that a backend is healthy or listening on that port.

## Verified boundary

The candidate was not executed. PowerShell 7.5.2 nonexecuting parser/AST checks
reported zero syntax errors and 16 source controls passed. An initial static
inspection command failed because its checker referenced a nonexistent AST
type; the checker was corrected before the recorded passing source controls.
This was a checker error, not a candidate syntax error. These results do not
prove Windows PowerShell 5.1 compatibility or runtime startup.

Backend health, native exit propagation, real client launch/reuse, process-path
access, file quoting/junctions, machine layout and shell policy remain unverified.
Process paths may be inaccessible, and path comparison does not prove a single
instance under races or path aliases. Hosts that block script files may refuse
the ordinary invocation; no policy workaround is provided.
No service, GUI, HTTP, download, login or actual media operation was run.
Existing UI/runtime configuration and external client installation remain the
operator's responsibility; this addition supplies neither.
