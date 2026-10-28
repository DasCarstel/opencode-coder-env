# skills/grafana – offizielle Grafana-Skills (vendored)

Drei Skills aus dem offiziellen, Apache-2.0-lizenzierten Repo
[`grafana/skills`](https://github.com/grafana/skills) (Branch `main`,
übernommener Commit: **`8fb72a5`**), **unverändert übernommen, nicht lokal
angepasst**:

| Ordner | Quelle im Upstream-Repo |
|--------|-------------------------|
| `grafana-oss/` | `skills/grafana-core/grafana-oss/` |
| `dashboarding/` | `skills/grafana-core/dashboarding/` |
| `alerting-irm/` | `skills/grafana-core/alerting-irm/` |

Die übrigen ~50 Upstream-Skills (`grafana-cloud`, `k6`, `app-sdk`, `alloy`,
`opentelemetry`, `promql`, `beyla`, `skill-authoring`, …) wurden bewusst **nicht**
übernommen — dieser Workspace betreibt nur die Grafana-OSS-Instanz auf dem
Docker-Host, nicht den Grafana-Cloud-Stack.

## Lizenz

Apache-2.0 — die Upstream-`LICENSE` liegt als [LICENSE](LICENSE) bei.

## Update-Prozedur

1. Neuen Stand holen: `git clone --depth 1 https://github.com/grafana/skills /tmp/grafana-skills`
   und Commit-Hash notieren (`git -C /tmp/grafana-skills rev-parse --short HEAD`).
2. Die drei Ordner neu rüberkopieren (gleiche Quelle → gleiches Ziel wie oben)
   und die `LICENSE` aktualisieren; Hash in dieser README anpassen.
3. Frontmatter der drei `SKILL.md` bleibt **byte-identisch zum Upstream**
   (nicht übersetzen, nicht kürzen) — nur die Umgebungs-Skills in
   `AGENTS.md`/`opencode.json` des Grafana-Spaces referenzieren sie.
