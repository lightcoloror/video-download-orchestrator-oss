# Video Download Orchestrator

Local URL routing, download planning, bounded execution and evidence handoff.
This public source edition uses Apache-2.0 for first-party code and synthetic
fixtures. External downloaders, browser installations, credentials, cookie
files, real captures and helper binaries are operator-provided.

## Install and inspect the offline contract

```sh
python -m pip install ".[portability]"
vdo --help
vdo portable-doctor --format json
vdo route https://example.invalid/synthetic.mp4 --format json
```

The route command classifies a URL. The portable doctor validates packaged
discovery and synthetic fixtures. Download execution and real account access
have separate explicit commands and configuration.

The runtime reads VIDEO_ORCHESTRATOR_CONFIG when set, then a checkout-local
config, then packaged defaults. Relative runtime state is stored under the
current project or working directory. The public example queues WeChat imports
and disables the external public-profile service until configured. Site probes
require an operator-configured loopback proxy. A configured endpoint is not
evidence of a running service.

## Offline regression

```sh
python -m pip install ".[test]"
python -m pytest -q
```

Tests use synthetic records, mock providers and loopback servers. Backend binary
integration tests require explicit opt-in. The private WeChat helper and local
Windows service launchers are excluded. Browser integration source is provided
for explicit operator review and setup, without automatic installation.

Artifact Lifecycle Core is pinned to its public source revision. The public
edition retains the existing lifecycle checks and planning/approval boundaries.
VKP handoff suggestions use the public video-knowledge CLI and relative output
paths. See LICENSE, THIRD_PARTY_NOTICES.md and AGENT_DISCOVERY.md.


### Windows source-checkout CLI helpers

The existing `scripts/vdo.ps1` entry and its shared `runtime-python.ps1` selector are included unchanged. See [the operator guide](docs/windows-cli-entry.md) for environment and argument behavior. This source edition has nonexecuting syntax and source checks only; actual CLI, download and interpreter behavior remain unverified.

See [docs/windows-ui-lifecycle.md](docs/windows-ui-lifecycle.md) for the VDO Windows UI launcher scripts (`start-ui.ps1`, `status-ui.ps1`, `stop-ui.ps1`, `serve-ui.ps1`, `start-ui-scheduled.ps1`), the managed-receipt and health controls, and the optional explicit PortRecord path. Verified so far: eight static checks pass and the five launcher scripts parse without Windows PowerShell 5.1 syntax errors. Synthetic fixture setup fails on a permission refusal while creating temporary test files, so the wrapper runtime controls, the bad-copy control, and real UI, scheduler, process, and knowledge-base behavior remain unverified; no runtime pass is claimed.

## Explicit Python workload entry sources

See [entry wrappers and process effects](docs/explicit-python-entry-sources.md) for backend integration and stdio MCP source references. Syntax and actual workloads remain unverified; no installation or execution is implied.
