---
name: authentik
description: Authentik SSO configuration via API on Docker Host. Prefer native OIDC/SAML over Proxy. Check integrations.goauthentik.io first. Create applications, providers, group-based policies, outpost assignment. Requires bootstrap token for admin API access.
license: MIT
compatibility: opencode
metadata:
  api_base: https://auth.mueller-nas.de/api/v3
  hosts:
    - Docker
  requires:
    - ssh-multi MCP
---

# Authentik Configuration via API

This skill covers Authentik administration via its REST API from the Docker Host. All API calls use `ssh-multi exec({host:"Docker", command:"curl ..."})`.

## Integration Strategy — Proxy vs. Native SSO

**CRITICAL: Always prefer native SSO (OIDC/SAML) over Proxy/ForwardAuth when available.**

### Decision Flow

1. Check `https://integrations.goauthentik.io/<app-name>/` — does Authentik have a dedicated integration guide?
2. **If the guide describes OAuth2/OIDC or SAML** → use native SSO (OAuth2 Provider in Authentik)
3. **If the guide describes Proxy Provider** → use ForwardAuth (as documented in this skill)
4. **If no integration exists** → ForwardAuth as universal fallback (this skill covers that)

### Why Prefer Native SSO?

| Aspect | Native SSO (OIDC/SAML) | Proxy/ForwardAuth |
|--------|----------------------|-------------------|
| Session management | App manages its own session | Outpost checks every request |
| Logout propagation | App-internal logout, properly forwarded | No automatic logout forwarding |
| API access | App's own API tokens work independently | Requires `skip_path_regex` exceptions |
| User attributes | Claims/assertions with rich user data | Limited to forwardAuth response headers |
| Configuration | App-specific setup needed | Universal, works with any HTTP app |
| Security | Protocol-level, robust | Reverse-proxy-level, simpler |

### Known App Categories

**Apps with native SSO (OIDC/SAML) — use OAuth2 Provider:**
Immich, Jellyfin, Paperless-ngx, Nextcloud, OpenCloud, Vaultwarden, Grafana, Home Assistant, n8n, Stirling PDF, Inbox Zero, Super Productivity, Linkwarden, Excalidraw, and many more.
→ Check `https://integrations.goauthentik.io/<app-name>/` for the specific guide.

**Apps without native SSO — use Proxy Provider (ForwardAuth):**
Sonarr, Radarr, Prowlarr, SABnzbd, Homarr, Portainer, Audiobookshelf, Komga, Tautulli, and others.
→ Follow the proxy provider workflow in this skill.

**When a Proxy Provider guide exists on the integrations site**, it documents the *correct way* to set it up — including the recommended `external_host`, `internal_host`, `skip_path_regex`, and any app-specific config changes (like `AuthenticationMethod: External` in Sonarr). Always follow that guide first, then apply the universal policy stack from this skill.

### How to Read the Integration Guide

When browsing `https://integrations.goauthentik.io/<app-name>/`:
- Look for "**Preparation**" section — it states which auth method is used
- "**Choose a Provider type**" tells you if it's OAuth2, SAML, or Proxy
- The guide gives the exact provider settings (redirect URIs, scopes, etc.)

### Starting a New App Integration

```
1. git-docker-host → git_pull() on /etc/docker
2. Check https://integrations.goauthentik.io/<app-name>/ for native SSO possibility
3. If native SSO → create OAuth2 Provider (not covered here, follow the integration guide)
4. If Proxy/ForwardAuth → use this skill's proxy provider workflow
5. Ask the user which group to use for access control (admin, monitoring, etc.)
6. Apply standard policy stack (Default Deny → chosen Group → Superuser)
7. Verify login flow works
8. git-docker-host → git_add/git_commit/git_push if config files changed
```

### When Modifying This Skill File

After editing `/home/carst/opencode-config/skills/authentik/SKILL.md`:

```bash
# Local WSL repo — use the bash tool (NOT git-docker-host!)
cd /home/carst/opencode-config
git add skills/authentik/SKILL.md
git commit -m "fix(authentik): <description>"
git push
```

## API Access

### Token Levels

Authentik has two tokens in `/opt/docker-compose/authentik/.env`:

| Token | Source | Scope | Can list users/groups? |
|-------|--------|-------|:---:|
| **Bootstrap Token** | `AUTHENTIK_BOOTSTRAP_TOKEN` | Full admin | ✅ |
| Outpost Token | `AUTHENTIK_TOKEN` | Outpost-only | ❌ |

**Always use the bootstrap token for admin operations:**

```bash
curl -s -H "Authorization: Bearer changeme-initial-setup" "https://auth.mueller-nas.de/api/v3/..."
```

**Verify correct token access:**
```bash
# With bootstrap token — should return user count > 0
curl -s -H "Authorization: Bearer changeme-initial-setup" "https://auth.mueller-nas.de/api/v3/core/users/" | python3 -c "import sys,json; print(json.load(sys.stdin)['pagination']['count'])"

# With outpost token — returns 0 (incorrect!)
curl -s -H "Authorization: Bearer ${AUTHENTIK_TOKEN}" "https://auth.mueller-nas.de/api/v3/core/users/" | python3 -c "import sys,json; print(json.load(sys.stdin)['pagination']['count'])"
```

### Token Rotation Warning

The bootstrap token from `.env` may change after initial setup. If API returns auth errors, check the current token value in the `.env` file:
```bash
ssh-multi exec({host:"Docker", command:"cat /opt/docker-compose/authentik/.env | grep BOOTSTRAP_TOKEN"})
```

### Common Headers

```bash
curl_flags='-s -H "Authorization: Bearer changeme-initial-setup" -H "Content-Type: application/json"'
```

---

## Standard Policy Stack

Every application follows this Order-based policy chain (lower order = higher priority):

```
Order 0: Default Deny      → blocks everyone by default
Order 1: <group> binding   → allows specific group members
Order 2: Allow Superuser   → allows superusers (admin)
```

### How Policy Engine Works

- Authentik evaluates policies in ascending `order`
- A policy that returns `False` at a lower order blocks access immediately
- All policies at the same order must pass for access (with `policy_engine_mode: all`)
- Group bindings check `request.user.ak_groups.contains(group)`

### Recommended for new apps

Depending on the app type and which users need access:

| App Category | Example Apps | Policy Stack |
|-------------|-------------|--------------|
| Admin-only | Proxmox, Portainer, Traefik, UniFi, Zigbee2MQTT | Default Deny + `admin` Group + Superuser |
| Media | Sonarr, Radarr, Prowlarr, Jellyfin, SABnzbd | Default Deny + `medien-tools` Group + Superuser |
| Productivity tools | Stirling PDF, Excalidraw, Super Productivity | Default Deny + `produktivitaet` Group + Superuser |
| Monitoring | Uptime Kuma, Homarr, InfluxDB | Default Deny + `monitoring` Group + Superuser |
| Public | Homer dashboard, LibreSpeed | No Deny, just Group or Superuser |

**CRITICAL: Always ask the user which group to apply.** Never assume a group. Use the group query below to present available options.

---

## Resource Reference

### Key PKs (Persistent)

These are stable identifiers from the current setup:

**Policies:**
| Name | PK | Type |
|------|-----|------|
| Default Deny | `a57c88ba-9806-481f-8561-167d778c3992` | Expression |
| Allow Superuser | `56d06f38-849b-4b26-9bc6-00f95b98809e` | Expression |

**Groups:**
| Name | PK | Use Case |
|------|-----|----------|
| admin | `8711318b-4ec5-4d3d-ac47-880815d139c6` | Admin-only apps (Proxmox, Portainer, Traefik, etc.) |
| medien-tools | `ed65b62a-50ee-4c60-8760-57aeff091545` | Media apps (Sonarr, Radarr, Jellyfin, etc.) |
| monitoring | `196606f8-2b02-4925-a7ef-a0ecae87138e` | Monitoring/dashboard apps (Uptime Kuma, Homarr, etc.) |
| produktivitaet | `89af32ae-98e0-4bc6-a52d-166b134bbdb6` | Productivity tools (Stirling PDF, Excalidraw, etc.) |
| homeassistant-users | `ff5e7a4d-698c-4c31-8d9c-7efa1ba91d59` | Home Assistant |
| immich-users | `cb2a1625-510c-4afa-be51-442a0512cc4b` | Immich |
| immich-familie-users | `cb0f3c2b-14bb-47e7-ad40-c279423e5751` | Immich (family access) |
| jellyfin_users | `3ba66217-3c15-4d9f-855a-94cd4708ed37` | Jellyfin (LDAP) |
| opencloud-users | `e08ca572-229e-44d7-b2cd-d9d83717815d` | OpenCloud |
| passwoerter | `3f8274f8-54fe-4164-85bc-d47f23bd382a` | Vaultwarden/Passwords |
| authentik Admins | `72e579c7-2d52-4f6a-89c4-9b422d34309c` | Authentik administration |

To query all groups:
```bash
curl -s -H "Authorization: Bearer changeme-initial-setup" \
  "https://auth.mueller-nas.de/api/v3/core/groups/?page_size=50" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'{r[\"name\"]}: {r[\"pk\"]}') for r in d['results']]"
```

**Outpost:**
| Name | PK | Type |
|------|-----|------|
| Traefik Proxy Outpost | `2bf907a7-c598-4b5c-b27f-10cc0eb15b65` | proxy |

**Catch-All Provider/App:**
| Resource | PK |
|----------|-----|
| Traefik Forward Auth (Provider) | 1 (numeric) |
| Traefik Forward Auth (Application) | `aaaaaaaa-1111-aaaa-1111-aaaaaaaaaaa1` |

### Dynamic PKs (Query Required)

Provider and Application PKs for specific services must be queried:

```bash
# List all applications with their PKs
curl -s -H "Authorization: Bearer changeme-initial-setup" \
  "https://auth.mueller-nas.de/api/v3/core/applications/?page_size=50" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'{r[\"pk\"]} | {r[\"name\"]} | provider={r.get(\"provider\")}') for r in d['results']]"

# List all proxy providers
curl -s -H "Authorization: Bearer changeme-initial-setup" \
  "https://auth.mueller-nas.de/api/v3/providers/proxy/" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'{r[\"pk\"]} | {r[\"name\"]} | mode={r[\"mode\"]} | outposts={r.get(\"outpost_set\",[])}') for r in d['results']]"
```

---

## Common Operations

### 1. Create a New Application + Provider

The provider type depends on the app's auth method:
- **Traefik forwardAuth** → Proxy Provider (`forward_single`)
- **OAuth2/OIDC apps** → OAuth2 Provider

#### Proxy Provider (for Traefik forwardAuth)

**CRITICAL: Always create the provider first, then create the application with the provider PK.** Creating the application without a provider and patching it later can result in the provider missing `assigned_application_slug`, which causes "no app for hostname" errors from the outpost.

```bash
# Step 1: Create Proxy Provider (without app link yet)
PROV_PK=$(curl -s -X POST \
  -H "Authorization: Bearer changeme-initial-setup" \
  -H "Content-Type: application/json" \
  -d '{
    "name":"MyApp",
    "mode":"forward_single",
    "external_host":"https://myapp.mueller-nas.de",
    "internal_host":"http://myapp",
    "authorization_flow":"664c7d78-3d82-4743-9539-fdf14aeb9b85",
    "invalidation_flow":"f0281d85-c8ca-43e3-a539-c0d7270168d3",
    "skip_path_regex":"^/api/.*"
  }' \
  "https://auth.mueller-nas.de/api/v3/providers/proxy/" | python3 -c "import sys,json; print(json.load(sys.stdin)['pk'])")
echo "Provider PK: $PROV_PK"

# Step 2: Create Application WITH the provider PK already set
APP_PK=$(curl -s -X POST \
  -H "Authorization: Bearer changeme-initial-setup" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"MyApp\",\"slug\":\"myapp-sso\",\"provider\":$PROV_PK}" \
  "https://auth.mueller-nas.de/api/v3/core/applications/" | python3 -c "import sys,json; print(json.load(sys.stdin)['pk'])")
echo "App PK: $APP_PK"

# Step 3: Verify the bidirectional link is established
curl -s -H "Authorization: Bearer changeme-initial-setup" \
  "https://auth.mueller-nas.de/api/v3/providers/proxy/$PROV_PK/" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); slug=d.get('assigned_application_slug'); assert slug is not None, 'FATAL: assigned_application_slug is None — outpost will ignore this provider!'; print(f'OK: assigned_application_slug={slug}')"
```

**Why slug naming convention matters:** Use `<service>-sso` for Proxy provider slugs (e.g., `sonarr`, `radarr` for arr apps; `seerr-sso`, `homer-sso` for others). The outpost matches requests by `external_host`, but if you later need to create a new slug for the same service, a stale slug may still block recreation. The `-sso` suffix avoids this collision.

**Key fields explained:**
- `external_host`: The public URL of the app (e.g., `https://sonarr.mueller-nas.de`)
- `internal_host`: Hostname resolvable from the outpost container (e.g., `http://sonarr`)
- `skip_path_regex`: Regex for paths that bypass auth (typically `^/api/.*` for API endpoints)
- `authorization_flow`/`invalidation_flow`: Flow UUIDs — can be copied from existing provider

### 2. Add Policies to Application

#### Add Default Deny (Order 0)

```bash
curl -s -X POST \
  -H "Authorization: Bearer changeme-initial-setup" \
  -H "Content-Type: application/json" \
  -d '{
    "policy":"a57c88ba-9806-481f-8561-167d778c3992",
    "target":"<APP_PK>",
    "order":0
  }' \
  "https://auth.mueller-nas.de/api/v3/policies/bindings/"
```

#### Add Group Binding (Order 1)

**CRITICAL: Ask the user which group to use.** Choose from the groups table above based on the app category.

```bash
curl -s -X POST \
  -H "Authorization: Bearer changeme-initial-setup" \
  -H "Content-Type: application/json" \
  -d '{
    "group":"8711318b-4ec5-4d3d-ac47-880815d139c6",
    "target":"<APP_PK>",
    "order":1
  }' \
  "https://auth.mueller-nas.de/api/v3/policies/bindings/"
```

Or for a different group, first find the group PK:
```bash
curl -s -H "Authorization: Bearer changeme-initial-setup" \
  "https://auth.mueller-nas.de/api/v3/core/groups/?name=<group_name>" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'{r[\"name\"]}: {r[\"pk\"]}') for r in d['results']]"
```

#### Add Allow Superuser (Order 2)

```bash
curl -s -X POST \
  -H "Authorization: Bearer changeme-initial-setup" \
  -H "Content-Type: application/json" \
  -d '{
    "policy":"56d06f38-849b-4b26-9bc6-00f95b98809e",
    "target":"<APP_PK>",
    "order":2
  }' \
  "https://auth.mueller-nas.de/api/v3/policies/bindings/"
```

#### Delete a Binding

```bash
curl -s -X DELETE \
  -H "Authorization: Bearer changeme-initial-setup" \
  "https://auth.mueller-nas.de/api/v3/policies/bindings/<BINDING_PK>/" \
  -w "\nHTTP %{http_code}"
```

### 3. Assign Provider to Outpost

```bash
# Step 1: Get current outpost configuration
curl -s -H "Authorization: Bearer changeme-initial-setup" \
  "https://auth.mueller-nas.de/api/v3/outposts/instances/2bf907a7-c598-4b5c-b27f-10cc0eb15b65/" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('Current providers:', d['providers'])"

# Step 2: Update with new provider added
# Get current_providers from above, add new provider PK to the list
curl -s -X PATCH \
  -H "Authorization: Bearer changeme-initial-setup" \
  -H "Content-Type: application/json" \
  -d '{"providers":[1, 23, 24, 25, 26, <NEW_PROVIDER_PK>]}' \
  "https://auth.mueller-nas.de/api/v3/outposts/instances/2bf907a7-c598-4b5c-b27f-10cc0eb15b65/"
```

**After assignment**, the outpost caches its config and refreshes every 5 minutes (`refresh_interval_s: 300`). If the provider doesn't work immediately, restart the outpost container:

```bash
docker restart authentik-outpost-1
```

### 4. Verify Everything

```bash
# Check bindings on an application
app_pk="<APP_PK>"
curl -s -H "Authorization: Bearer changeme-initial-setup" \
  "https://auth.mueller-nas.de/api/v3/policies/bindings/?target=$app_pk" | \
  python3 -c "
import sys,json
d=json.load(sys.stdin)
for r in sorted(d['results'], key=lambda r: r['order']):
    p = r.get('policy_obj') or {}
    g = r.get('group_obj') or {}
    n = p.get('name') if p else (g.get('name') if g else '?')
    print(f'  Order {r[\"order\"]}: {n}')
"

# Check which providers are assigned to the outpost
curl -s -H "Authorization: Bearer changeme-initial-setup" \
  "https://auth.mueller-nas.de/api/v3/outposts/instances/2bf907a7-c598-4b5c-b27f-10cc0eb15b65/" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('Assigned providers:', d['providers'])"

# Check provider details (external_host, internal_host, mode)
curl -s -H "Authorization: Bearer changeme-initial-setup" \
  "https://auth.mueller-nas.de/api/v3/providers/proxy/<PROVIDER_PK>/" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(f'mode={d[\"mode\"]} ext={d[\"external_host\"]} int={d[\"internal_host\"]} outposts={d.get(\"outpost_set\",[])} skip={d.get(\"skip_path_regex\",\"\")}')"

# CRITICAL: Verify provider has an application linked (assigned_application_slug must NOT be null)
curl -s -H "Authorization: Bearer changeme-initial-setup" \
  "https://auth.mueller-nas.de/api/v3/providers/proxy/<PROVIDER_PK>/" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); slug=d.get('assigned_application_slug'); assert slug is not None, 'FATAL: assigned_application_slug is None — outpost will ignore this provider!'; print(f'OK: assigned_application_slug={slug}')"
```

---

## Traefik Integration

### How forwardAuth Works in This Setup

1. Traefik labels on the service container define: `middlewares=authentik@docker`
2. The `authentik@docker` middleware is defined on the Authentik server container:
   ```yaml
   traefik.http.middlewares.authentik.forwardauth.address=http://192.168.80.92:9000/outpost.goauthentik.io/auth/traefik
   traefik.http.middlewares.authentik.forwardauth.trustForwardHeader=true
   ```
3. The Outpost container receives the forwardAuth request and matches the `Host` header against assigned providers' `external_host`
4. If matched → applies that provider's policies → returns 200 (allow) or 302 (redirect to login)
5. If no match → falls through to the catch-all provider (Traefik Forward Auth, provider 1)

### Required Service Labels (on Docker Host)

For a service to use Authentik SSO, add these Traefik labels to its docker-compose:

```yaml
labels:
  - "traefik.http.routers.myapp-secure.middlewares=authentik@docker"
  - "traefik.docker.network=proxy"
```

### When to Skip API Paths

Set `skip_path_regex: "^/api/.*"` on the proxy provider if:
- The app has API endpoints that receive automated requests (not through browser)
- E.g., Prowlarr queries Sonarr API, Sonarr queries SABnzbd API
- Inter-service API traffic that goes through Traefik needs this exception

If inter-service communication uses Docker network IPs directly, `skip_path_regex` is optional but harmless.

---

## Troubleshooting

### "0 users, 0 groups" from API

**Cause:** Using the outpost token (`AUTHENTIK_TOKEN`) instead of bootstrap token.
**Fix:** Use `AUTHENTIK_BOOTSTRAP_TOKEN` from the `.env` file.

### "Internal host cannot be empty"

**Cause:** PATCH on provider requires `internal_host` field.
**Fix:** Include `"internal_host":"http://<container_name>"` in every PATCH, even if you're only updating another field.

### Duplicate Group Bindings

**Cause:** Creating a binding that already exists returns `"The fields policy, target, order must make a unique set."` — sometimes the binding was created previously.

**To clean up duplicates:**
```bash
# Find all bindings for an app, filtered by group
target="<APP_PK>"
group="8711318b-4ec5-4d3d-ac47-880815d139c6"
curl -s -H "Authorization: Bearer changeme-initial-setup" \
  "https://auth.mueller-nas.de/api/v3/policies/bindings/?target=$target&group=$group" | \
  python3 -c "
import sys,json,subprocess
d=json.load(sys.stdin)
bindings = sorted(d['results'], key=lambda r: r['pk'])
if len(bindings) > 1:
    duplicate = bindings[1]['pk']
    print(f'Deleting duplicate: {duplicate}')
    subprocess.run(['curl','-s','-X','DELETE','-H','Authorization: Bearer changeme-initial-setup',
                    f'https://auth.mueller-nas.de/api/v3/policies/bindings/{duplicate}/'])
else:
    print('No duplicates')
"
```

### Access Denied Despite Correct Policies

Check:
1. Provider has `external_host` matching the actual URL used in browser
2. Provider is assigned to an outpost (`outpost_set` is not empty)
3. **Provider has `assigned_application_slug` set (not null)** — run the verify command from section 4
4. Policy order is correct (Deny=0, Group=1, Superuser=2)
5. User is actually in the group (check in Authentik UI or API)
6. Traefik middleware `authentik@docker` is in the service's labels

### "no app for hostname" in Outpost

**Symptom:** Browsing to `https://myapp.mueller-nas.de` shows Authentik error "no app for hostname". Outpost logs show `"event":"no app for hostname","host":"myapp.mueller-nas.de"`.

**Root cause:** The outpost has the provider in its `providers_obj` list, but the provider's `assigned_application_slug` is `null`. The outpost silently ignores providers without a linked application during hostname matching.

**Verification:**
```bash
curl -s -H "Authorization: Bearer changeme-initial-setup" \
  "https://auth.mueller-nas.de/api/v3/providers/proxy/<PROVIDER_PK>/" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(f'assigned_application_slug={d.get(\"assigned_application_slug\")}')"
```

**Fix:** Create the application with the provider PK already set (see Full Example). If the provider already exists without a link, create a new application linked to that provider:

```bash
APP_PK=$(curl -s -X POST \
  -H "Authorization: Bearer changeme-initial-setup" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"MyApp\",\"slug\":\"myapp-sso\",\"provider\":<PROVIDER_PK>}" \
  "https://auth.mueller-nas.de/api/v3/core/applications/" | python3 -c "import sys,json; print(json.load(sys.stdin)['pk'])")
```

Then restart the outpost: `docker restart authentik-outpost-1`

---

## Full Example: Adding a New Proxy-Protected App

Complete walkthrough for adding a new service (e.g., "MyService" at `myservice.mueller-nas.de`):

```bash
TOKEN="changeme-initial-setup"
BASE="https://auth.mueller-nas.de/api/v3"
OUTPOST="2bf907a7-c598-4b5c-b27f-10cc0eb15b65"
DENY="a57c88ba-9806-481f-8561-167d778c3992"
SUPER="56d06f38-849b-4b26-9bc6-00f95b98809e"
# IMPORTANT: Ask the user which group to use. Examples:
#   admin: 8711318b-4ec5-4d3d-ac47-880815d139c6
#   medien-tools: ed65b62a-50ee-4c60-8760-57aeff091545
#   monitoring: 196606f8-2b02-4925-a7ef-a0ecae87138e
#   produktivitaet: 89af32ae-98e0-4bc6-a52d-166b134bbdb6
GROUP="8711318b-4ec5-4d3d-ac47-880815d139c6"  # admin group

# 1. Create Proxy Provider FIRST (without app link)
PROV_PK=$(curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"MyService\",\"mode\":\"forward_single\",\"external_host\":\"https://myservice.mueller-nas.de\",\"internal_host\":\"http://myservice\",\"authorization_flow\":\"664c7d78-3d82-4743-9539-fdf14aeb9b85\",\"invalidation_flow\":\"f0281d85-c8ca-43e3-a539-c0d7270168d3\",\"skip_path_regex\":\"^/api/.*\"}" \
  "$BASE/providers/proxy/" | python3 -c "import sys,json; print(json.load(sys.stdin)['pk'])")

# 2. Create Application WITH provider PK (bidirectional link established immediately)
APP_PK=$(curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"MyService\",\"slug\":\"myservice-sso\",\"provider\":$PROV_PK}" \
  "$BASE/core/applications/" | python3 -c "import sys,json; print(json.load(sys.stdin)['pk'])")

# 3. VERIFY the provider has assigned_application_slug set
curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE/providers/proxy/$PROV_PK/" | python3 -c "
import sys,json
d=json.load(sys.stdin)
slug=d.get('assigned_application_slug')
if slug is None:
    print('FATAL: assigned_application_slug is None — outpost will ignore this provider!')
    sys.exit(1)
print(f'OK: assigned_application_slug={slug}')
"

# 4. Add Policy Bindings
for binding in \
  "{\"policy\":\"$DENY\",\"target\":\"$APP_PK\",\"order\":0}" \
  "{\"group\":\"$GROUP\",\"target\":\"$APP_PK\",\"order\":1}" \
  "{\"policy\":\"$SUPER\",\"target\":\"$APP_PK\",\"order\":2}"; do
  curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$binding" "$BASE/policies/bindings/" > /dev/null
done

# 5. Assign to Outpost
CURRENT=$(curl -s -H "Authorization: Bearer $TOKEN" "$BASE/outposts/instances/$OUTPOST/" | \
  python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)['providers']+[$PROV_PK]))")
curl -s -X PATCH \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"providers\":$CURRENT}" \
  "$BASE/outposts/instances/$OUTPOST/"

# 6. Restart Outpost to pick up new provider
docker restart authentik-outpost-1

echo "Done! Provider PK: $PROV_PK, Application PK: $APP_PK"
```

---

## API Endpoint Reference

| Action | Method | Endpoint |
|--------|--------|----------|
| List applications | GET | `/core/applications/?page_size=50` |
| Get application | GET | `/core/applications/{uuid}/` |
| Create application | POST | `/core/applications/` |
| Update application | PATCH | `/core/applications/{uuid}/` |
| List proxy providers | GET | `/providers/proxy/` |
| Create proxy provider | POST | `/providers/proxy/` |
| Update provider | PATCH | `/providers/proxy/{pk}/` |
| List users | GET | `/core/users/` |
| List groups | GET | `/core/groups/` |
| Get group with members | GET | `/core/groups/{uuid}/` |
| List policies | GET | `/policies/all/?page_size=50` |
| List bindings (by target) | GET | `/policies/bindings/?target={uuid}` |
| List bindings (by policy) | GET | `/policies/bindings/?policy={uuid}` |
| Create binding | POST | `/policies/bindings/` |
| Delete binding | DELETE | `/policies/bindings/{uuid}/` |
| Get outpost | GET | `/outposts/instances/{uuid}/` |
| Update outpost | PATCH | `/outposts/instances/{uuid}/` |

---

## When to Load This Skill

Load this skill when:
- Adding a new service to Authentik SSO (**first check `https://integrations.goauthentik.io/<app>/` for native OIDC/SAML support**)
- Configuring or modifying Authentik policies
- Troubleshooting Authentik access issues
- Querying Authentik users, groups, or applications from the API
- Setting up Proxy Providers (ForwardAuth) for apps without native SSO
- Applying the standard policy stack (Default Deny → chosen Group → Superuser) — **always ask which group to use**
