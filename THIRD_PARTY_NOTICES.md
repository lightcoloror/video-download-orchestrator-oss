# Component boundaries

First-party source and synthetic fixtures in this edition use Apache-2.0.
Python packages are independently installed dependencies and retain their
distribution licenses and notices. Python is an external runtime.

External downloader programs, MediaGo, browser installations, third-party
services and media are not bundled. Their own licenses and operator-provided
authorization apply. yt-dlp is used through its installed Python/CLI interface.
Artifact Lifecycle Core is a separately licensed Apache-2.0 public dependency.

The WeChat helper implementation, credentials, cookie files, real captures,
private source history and machine-specific launchers are excluded.


## Excluded WeChat helper provenance

The optional helper implementation remains excluded. Its translated decryption
source cites noway2013/WechatSphDecrypt and ltaoo/wx_channels_download, without
recording the upstream revisions actually used. The project's Apache-2.0 grant
does not establish permission over that referenced code.

At this review, the complete retrieved
[WechatSphDecrypt tree](https://github.com/noway2013/WechatSphDecrypt/tree/62d8134d37b67a5500d7716e2cea6b0956eb5cad)
contained no LICENSE, COPYING or NOTICE file. This is a bounded file-tree
observation, not a claim that no permission could exist elsewhere.
The reviewed
[wx_channels_download license](https://github.com/ltaoo/wx_channels_download/blob/83ef4b634d7996695d66f432fb39e851956bd3a2/LICENSE)
is MIT subject to the Commons Clause condition restricting the right to Sell;
it is not treated as an unrestricted SPDX MIT grant.

These current snapshots do not identify the historical reuse revision or a
separate permission grant. Redistribution of the excluded implementation
therefore remains unverified. This public edition supplies its existing
first-party transport integration only; it does not distribute helper source,
decryption implementation, credentials, media, or external programs.
