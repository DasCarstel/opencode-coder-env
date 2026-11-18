# Health – Herdr-Space im general-Profil

## Kontext
Health-Space innerhalb des general-Profils (eigener Herdr-Workspace
`workspaces/general/health`, Agent `opencode-health`, Netz `coder-gen`, kein
ssh-mcp). Hier läuft das Training- und Ernährungs-Management: **Training via
openGym-MCP** (session-lokal in `opencode.json`), **Ernährung via yazio-MCP**,
**Picnic-Einkäufe via picnic-MCP**. ocis
und pdf kommen aus der globalen Config. **super-productivity ist bewusst NICHT
im Health-Space verfügbar** — es bleibt auf den General-Space beschränkt.

## MCP-Server (session-lokal)
- **opengym** (remote) – `https://gym.mueller-nas.de/mcp`, Bearer-Token aus
  OpenBao `secret/data/mcp/opengym` (Feld `API_TOKEN`)
- **yazio** (lokal) – `node /opt/yazio-mcp/dist/index.js` (Fork-Build; im
  general-Image gebacken oder per startup.sh nachinstalliert – Stempel
  `.yazio-mcp-version`)
  - Env: `YAZIO_USERNAME` / `YAZIO_PASSWORD` / `YAZIO_CLIENT_ID` /
    `YAZIO_CLIENT_SECRET` (aus OpenBao `secret/data/mcp/yazio`)
  - Token-Cache: `/root/.yazio/token.json`
- **picnic** (lokal) – `node /usr/lib/node_modules/mcp-picnic/bin/mcp-server.js`
  (npm-Paket `mcp-picnic@1.15.1`; im general-Image gebacken oder per startup.sh
  nachinstalliert – Stempel `.mcp-picnic-version`; der npm-Bin-Eintrag heißt
  irreführend `mcp-server-template`, deshalb direkter Dateipfad)
  - Env: `PICNIC_USERNAME` / `PICNIC_PASSWORD` / `PICNIC_COUNTRY_CODE`
    (aus OpenBao `secret/data/mcp/picnic`)
  - 2FA/Session-Files: `/root/.picnic/picnic-session.json` und
    `/root/.picnic/picnic-device.json` (bleiben im Container erhalten)

## MCP-Server (geerbt aus workspace-global.json des general-Profils)
- **ocis** (lokal) – privater Obsidian-Vault (`OCIS_PRIVATE_USER`/`_TOKEN`)
- **pdf** (lokal) – `npx @sylphx/anymd@8.5.1`

## OpenBao
Die AppRole des general-Profils `mcp-general` (Policy `mcp-server-general`)
liest zusätzlich `secret/data/mcp/picnic` und `secret/data/mcp/yazio`. Nicht
die gemeinsame `mcp-server`.

## Richtlinien
- Kein ssh-mcp — dieser Space hat keinen SSH-Zugang auf irgendeinen Host
- Kein internes VLAN (Krefeld/Kleve) — kein Zugriff
- Keine Secrets, Tokens oder private Keys im Repo
