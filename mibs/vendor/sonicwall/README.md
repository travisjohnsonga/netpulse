# SonicWall MIBs

Public source: https://github.com/sonicwall/sonicwall-mibs
(or https://www.sonicwall.com/support/technical-documentation/)

`scripts/download_mibs.sh` fetches these automatically. Key OIDs NetPulse polls
(SONICWALL enterprise 8741): `sonicCpuUtil`, `sonicRamUtil`, `sonicRamTotal`,
`sonicCurrentConnections`.

Place downloaded `.mib`/`.my` files in this directory.
