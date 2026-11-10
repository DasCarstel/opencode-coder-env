---
name: space-matrix
description: PFICHT bei Aufgaben mit „OpenCode“/„delegiere“ und bei mehrstufigen Analyse-, Coding-, Automations-, Home-Assistant-, Server- oder Obsidian-Aufträgen. REST-Feuern, im selben Turn warten, Rückfrage über clarify oder als Text, Antwort per Formular-Reply, Fortsetzen.
---

# OpenCode-Delegation (REST, im selben Turn)

**kleve-Zusatz (überschreibt das „wortgetreu“):** Fragen und Ergebnisse dem Vater in
einfacher Sprache vorlegen (keine Fachwörter, IDs, Befehle). Die exakten Auswahl-Werte
intern merken und für das Formular-Reply unverändert nutzen.

```bash
P=$(grep '^OPENCODE_SERVER_PASSWORD=' /opt/data/.env | cut -d= -f2- | sed 's/^"//; s/"$//'); test -n "$P" || exit 1
OC=http://coder-carstenmueller2002-infrastructure:49374
WS=/home/carstenmueller2002/opencode-coder-env/workspaces
DISPATCH="/opt/hermes/.venv/bin/python /opt/data/bin/oc-dispatch.py"
```

**directory:** general · minijob · `$WS/infrastructure/{homeassistant,server-management,svelte,cleve}`

**Muster 0 — keine Eigenarbeit.** Delegierbar ⇒ sofort Muster 1, ohne Vorab-Nachforschung. Fehlende Parameter ⇒ EINE Rückfrage.

**Muster 1 — Auftrag geben, im selben Turn warten**

Aufträge ans Haus in Kleve gehen an die Workspace-`directory` `/home/carstenmueller2002/opencode-coder-env/workspaces/infrastructure/cleve` (Pflicht-Pfad, sonst fehlen Home-Assistant-Kleve und SSH).

1) Auftrag und Versand in EINEM Aufruf. Das Modell setzt `$DISPATCH` selbst (Hestia-Tier):

```bash
cat > /tmp/oc-auftrag.txt <<'EOF'
<Auftrag wortgetreu>. Bei Entscheidungen Question-Werkzeug nutzen, auf Antwort warten.
EOF
$DISPATCH --dir "<DIR>" --title "H: <kurz> <YYYYMMDD-HHMM>" --task-file /tmp/oc-auftrag.txt
```

Ausgabe: `SID=` (gilt für alle Fenster), `STATUS=new|reused|adopted|explicit`,
`PROMPT=sent|skipped`. `reused|adopted|skipped` sind normal; bei `skipped` direkt zu 2.

2) **Warten im selben Turn.** Ein Fenster = EIN `terminal`-Aufruf mit `timeout` größer als
die Fensterdauer (Schleife ~4 min ⇒ `timeout=300`):

```bash
for i in $(seq 1 30); do sleep 8; F=$(curl -s -m 5 -u "opencode:$P" "$OC/api/session/<sid>/form"); case "$F" in *frm_*) echo FORM-EVENT:; echo "$F"; exit 0;; esac; S=$(curl -s -m 5 -u "opencode:$P" "$OC/api/session/<sid>"); case "$S" in *'"outcome":"succeeded"'*|*'"outcome":"failed"'*) echo DONE-EVENT:; echo "$S"; exit 0;; esac; done; echo NOEVENT
```

Fertig wird am Feld **`outcome`** erkannt, nie an `/api/session/active` (nicht generierende
Sessions fehlen dort). Fragen nur über `…/<sid>/form`, nicht über `/api/form`.

3) Fenster auswerten:
- `FORM-EVENT` ⇒ Frage im selben Turn **wortgetreu** in einfacher Sprache vorlegen: mit
  `clarify`, wenn verfügbar, sonst als Text mit allen Optionen. Nie selbst entscheiden.
  Nach der Antwort Formular-Reply senden, weiter bei 2.
- `DONE-EVENT` ⇒ letzte assistant-Message (`…/<sid>/message?limit=3`) in einfacher Sprache
  berichten, Turn beenden.
- `NOEVENT` oder Abbruch (`exit_code 130`) ⇒ nächstes Fenster, zurück zu 2. Nie „fertig".

**Muster 2 — Antwort zurückschreiben**

`curl -s -u "opencode:$P" -X POST -H 'Content-Type: application/json' -d '{"answer":{"<key>":"<Wert>"}}' "$OC/api/session/<sid>/form/<formID>/reply"` → weiter bei 2.
Kommt die Antwort als **neue Nachricht** (naechster Turn, z. B. in Open WebUI): **NIEMALS eine neue Session anlegen.** Es laeuft noch eine Facharbeiter-Session mit offenem Formular - die muss gefunden und beantwortet werden:

1) Laufende Sessions holen und je …/<sid>/form pruefen:
   curl -s -u "opencode:$P" "$OC/api/session/active"
   curl -s -u "opencode:$P" "$OC/api/session/<sid>/form"
   Die Session mit offenem Formular (frm_...) und passendem Thema ist die richtige.
2) Formular-Reply dorthin (Kommando oben), weiter bei 2 des Musters 1.
3) Nur wenn es KEINE Session mit offenem Formular gibt: **Fortsetzen mit `--sid` — und NUR die Antwort senden, niemals den ganzen Auftrag wiederholen:**
   `$DISPATCH --sid <sid> --title "<exakt derselbe Titel wie beim ersten Mal>" --text "Antwort des Vaters auf deine Rueckfrage: <nur die Antwort, ohne den Auftrag zu wiederholen>"`
   `--sid` setzt STATUS=explicit und trifft dieselbe Session; ihr Verlauf (Auftrag + Rueckfrage) bleibt voll erhalten. Den Auftrag wortgetreu erneut zu feuern erzeugt dagegen ein Duplikat in der Session und sieht fuer den Vater wie eine neue Session aus - verboten. Ein anderer Kurz-Titel oder `--sid` zu vergessen erzeugt eine zweite Session, und die Antwort geht verloren.

**Muster 3 — Status („Was läuft?")**

`…/<sid>/message?limit=5` · `…/<sid>/form` · `/api/session/active`. Frage ⇒ Muster 2 ·
SID nicht in `active` ⇒ fertig melden.

**Muster 4 — Fortsetzen/Abbrechen**

Fortsetzen: Muster 1 mit `--sid <sid>` statt über den Titel. `--force-prompt` nur für exakt
denselben Text. Abbrechen: `POST "$OC/api/session/<sid>/interrupt"`.

**Störungen**

- Leerer erster Turn (`"finish":"error"`, leerer `content`) = Modell-/Providerfehler, nicht
  das Thema. Signatur und Vorgehen: `references/provider-fehler-und-session-modell.md`.
- Session-Modell statt Fleet-Tier ändern: `POST "$OC/api/session/<sid>/model"` mit
  `{"model":{"id":"…","providerID":"…","variant":"high"}}` (204), danach
  `--sid <sid> --force-prompt`.

**Regeln**

- Session-Anlage und Prompt-Versand **nur** über `$DISPATCH`; nie selbst `POST /api/session`.
- Nie ein Modell im Auftrag nennen — die Auswahl gehört `$DISPATCH` (Hestia-Tier).
- **Der Turn bleibt offen**, solange die Session läuft. Kein Turn-Ende nach dem Dispatch —
  sonst erreicht die Rückfrage den Vater nie.
- Wiederholen ist gefährlos: identischen Auftrag erneut, es entsteht keine zweite Session.
- Kein Turn ohne Werkzeugaufruf: erst `$DISPATCH` feuern, dann melden.
- `skill_view` kann `content_returned:false` (Dedup) liefern — dann SKILL.md direkt lesen.
- Kein Selbstbau über diese Muster hinaus; Debugging zuerst `$OC/openapi.json`.
