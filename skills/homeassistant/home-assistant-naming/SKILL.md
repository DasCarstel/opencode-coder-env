---
name: home-assistant-naming
description: Konventionen für Namen, Icons, Labels und Kategorien von Home-Assistant-Automationen, Skripten und Helfern. Laden, bevor eine Automation, ein Skript oder ein Helfer erstellt, umbenannt oder einsortiert wird.
license: MIT
---

# HA Naming, Icons, Labels & Kategorien

Verbindlich für diese Installation. Quelle der Wahrheit: Live-Registries
(`config/label_registry/list`, `config/category_registry/list?scope=…`).

## 1. Namen

| Typ | Format | Beispiel |
|---|---|---|
| Automation | `BEREICH \| Auslöser = Aktion` | `WECKER \| 30 min vorher = Klima vorheizen` |
| Automation (Dauer/ohne Auslöser) | `BEREICH \| Aktion` | `TISCH \| BLE Watchdog` |
| Skript | `BEREICH \| Aktion` | `ROBOROCK \| alles saugen` |
| Helfer | `Bereich: Zweck` | `Tisch Stehen: Montag` |

- BEREICH in GROSSBUCHSTABEN (Gerät oder Raum): `ZIMMER LICHT`, `TISCH`, `WECKER`, `BADEZIMMER`, `REINIGUNG`, `TRUENAS`.
- Trenner exakt ` | ` (Pipe mit Leerzeichen) und ` = `.
- Deutsch, echte Umlaute (ä ö ü ß) — keine ae/oe/ue/ss.
- Keine Präfixe wie "Automation"/"Skript", keine Zeitstempel, keine Emojis.
- Details in `description`, nicht im Namen. Entity-ID nie ändern, nur `alias`/`name`.

## 2. Icons

- Immer `mdi:`, funktionsbezogen, nie dekorativ.
- Zustand im Icon abbilden: `mdi:lightbulb-group` / `mdi:lightbulb-group-off`, `mdi:radiator` / `mdi:radiator-off`, `-alert`, `-outline` nutzen.
- Jede Automation/jedes Skript/jeder Helfer hat ein eigenes Icon (kein `mdi:automation`, kein leer).

## 3. Labels (= Tags)

Labels = **tatsächlich beteiligte Bausteine**, nur wenn im Code verwendet.
Mehrere Labels erlaubt, aber kein Label „auf Vorrat".

| Label (`label_id`) | Name | Icon | setzen wenn … |
|---|---|---|---|
| `helper` | Helper | mdi:help-rhombus | `input_*`, `counter`, `timer`, `schedule` benutzt |
| `message` | Message | mdi:message | `notify.*`, `persistent_notification`, `rest_command.ntfy`, Discord/Mail |
| `tur` | Tür | mdi:door-open | Türkontakt/Riegelschalter/Türöffner |
| `fenster` | Fenster | mdi:window-open | Fensterkontakt, Fenstergruppe |
| `licht` | Licht | mdi:lightbulb-on | `light.*`, Szenen mit Licht |
| `jalousie` | Jalousie | mdi:blinds | `cover.*` |
| `heizung` | Heizung | mdi:radiator | `climate.*`, TRV, Heizkörper |
| `klima` | Klima | mdi:air-conditioner | Klimaanlage, Split, Luftermodus |
| `luftreiniger` | Luftreiniger | mdi:air-filter | Luftreiniger, PM2.5 |
| `fan` | Fan | mdi:fan | `fan.*`, Ventilator, Windmaschine |
| `temperatur` | Temperatur | mdi:temperature-celsius | Temperatursensoren/-vergleiche |
| `feuchtigkeit` | Feuchtigkeit | mdi:water-percent | Luftfeuchte, absolute Feuchte |
| `helligkeit` | Helligkeit | mdi:sun-wireless-outline | Lux, Sonne, Dämmerung |
| `stromverbrauch` | Stromverbrauch | mdi:power-plug | Leistung, Energie, Preis |
| `zone` | Zone | mdi:map-clock | `person.*`, `zone.*`, `device_tracker.*` |
| `wecker` | Wecker | mdi:alarm | Wecker/Alarm-Entities |
| `prasenz` | Präsenz | mdi:motion-sensor | Bewegungs-/Präsenzmelder |
| `sleep_tracker` | Sleep Tracker | mdi:chat-sleep | Sleep-as-Android |
| `server` | Server | mdi:server | Serverdienste, Container, HACS |
| `truenas` | TrueNAS | mdi:nas | TrueNAS-Host, Jellyfin/Sonarr/Radarr/Sabnzbd |
| `bluetooth` | Bluetooth | mdi:bluetooth | BLE-Verbindungen |
| `3d_drucker` | 3D Drucker | mdi:printer-3d-nozzle | 3D-Drucker |
| `tisch` | Tisch | mdi:desk | Schreibtisch, Stehen/Sitzen |
| `roborok` | Roborock | mdi:robot-vacuum | Saug-/Wischroboter |
| `reinigung` | Reinigung | mdi:broom | Reinigungsplan, Aufgaben |
| `aroma` | Aroma | mdi:scent | Aroma Diffuser |
| `bad` | Bad | mdi:shower | Badezimmer, Dusche, Zähne |
| `wasser` | Wasser | mdi:pipe-leak | Leck-/Wassersensoren |
| `klingel` | Klingel | mdi:doorbell | Türklingel, ESPBell |
| `gesundheit` | Gesundheit | mdi:heart-pulse | Waage, Körperdaten |
| `sprachassistent` | Sprachassistent | mdi:account-voice | Assist/Google/Alexa/Hermes |
| `abfall` | Abfall | mdi:trash-can | Mülltonnen |
| `ai_made` | AI made | mdi:brain | von KI erstellt und noch nicht vom Nutzer überarbeitet |

`ai_made` niemals bei Nutzerarbeit setzen; nach manueller Überarbeitung entfernen.

## 4. Kategorien (= Gruppen)

Genau **eine** Kategorie pro Entity, Scope beachten. Vorhandene verwenden,
nur bei echtem Fehlen neu anlegen (Name + Icon wie unten).

**automation:** 3D Drucker `mdi:printer-3d`, Aroma Diffuser `mdi:scent`, Bad
`mdi:shower`, Belüftung `mdi:weather-windy`, Gesundheit `mdi:heart-pulse`,
Heizung `mdi:radiator`, Jalousie `mdi:blinds`, Kalender `mdi:calendar`, Klima
`mdi:air-conditioner`, Licht `mdi:lightbulb-group`, Luftreiniger
`mdi:air-purifier`, Präsenz `mdi:account-check`, Reinigung `mdi:broom`,
Roborock `mdi:robot-vacuum`, Schlaf `mdi:power-sleep`, Server `mdi:server`,
Sicherheit `mdi:security`, Sprachassistent `mdi:account-voice`, Tisch
`mdi:desk`, TrueNAS `mdi:nas`, Tür & Klingel `mdi:doorbell`, Wecker `mdi:alarm`

**script:** Aroma Diffuser `mdi:scent`, Bad `mdi:shower`, Heizung
`mdi:radiator`, Helper `mdi:progress-helper`, Jalousie `mdi:blinds`, Klima
`mdi:air-conditioner`, Licht `mdi:lightbulb-group`, Luftreiniger
`mdi:air-purifier`, Play `mdi:volume-medium`, Reinigung `mdi:broom`, Roborock
`mdi:robot-vacuum`, Sicherheit `mdi:security`, Sprachassistent
`mdi:account-voice`, Tisch `mdi:desk`, TrueNAS `mdi:nas`

**helpers:** Aroma Diffuser `mdi:scent`, Bad `mdi:shower`, Fenster
`mdi:window-open`, Gesundheit `mdi:heart-pulse`, Jalousie `mdi:blinds`, Klima
`mdi:air-conditioner`, Licht `mdi:lightbulb-group`, Präsenz
`mdi:account-check`, Reinigung `mdi:broom`, Roborock `mdi:robot-vacuum`,
Schlaf `mdi:power-sleep`, Sensor `mdi:weather-windy`, Sprachassistent
`mdi:account-voice`, Tisch `mdi:desk`, TrueNAS `mdi:nas`, Tür & Klingel
`mdi:doorbell`, Wecker `mdi:alarm`

## 5. Skill aktuell halten (Pflicht)

Dieser Skill ist die Doku der Live-Registries und muss **im selben Arbeitsgang**
mitgepflegt werden:

- Neues Label → Zeile in der Label-Tabelle (§3) ergänzen (`label_id`, Name, Icon, Zweck).
- Neue Kategorie → im passenden Scope-Block (§4) mit Name + Icon ergänzen.
- Umbenanntes Label/eine umbenannte Kategorie → beide Stellen (Name und ggf. `label_id`) angleichen.
- Gelöschtes Label/eine gelöschte Kategorie → aus §3/§4 entfernen.

Nach jeder Änderung gegen die Live-Registries prüfen
(`config/label_registry/list`, `config/category_registry/list?scope=…`) und den
Skill im selben Commit wie die HA-Änderung versionieren und pushen.

## 6. Helfer: Typen & Editierbarkeit

- **UI-/Config-Entry-Helper sind editierbar:** `input_*`, `counter`, `timer`,
  `schedule` sowie Template-/Integrations-Helper (Config-Flow). Sie liegen in
  `.storage` und haben `config_entry_id`.
- **Legacy-YAML-Templates sind NICHT editierbar** (`template:` in
  `configuration.yaml` / `packages/*.yaml`). State-only-Templates dorthin
  migrieren; Entity-ID muss exakt erhalten bleiben.
- **Grenzen der UI-Template-Helper:** kein `trigger:`, keine `attributes:`.
  - Trigger nur zur zeitlichen Aktualisierung (`time_pattern`) → `now()` im
    Template verwenden, UI-Helper rendert dann minütlich neu.
  - Attribute → nur nötig, wenn Dashboards/Automationen sie lesen; sonst
    weglassen und als normalen UI-Helper anlegen.
  - Echte Trigger-Logik oder benötigte Attribute → bleibt YAML.
- **YAML-Licht-/Gruppen-Gruppen** (`light: platform: group`) → UI-Gruppen-Helper
  (Config-Flow `group`, Domain `light`), Entity-ID bleibt bei gleichem Namen.
- **REST-Sensoren** (`rest:`) haben kein UI-Pendant und bleiben YAML
  (aktuell `binary_sensor.jellyfin_admin_spielt`).
- **Neue Helfer immer über UI/Config-Flow anlegen**, nie neu in YAML.
- Nach dem Anlegen Icon, Labels und Kategorie nach §2–§4 setzen; jeder Helfer
  bekommt `helper` plus die Zweck-Labels.

## 7. Umsetzen

- **Namen:** `alias:` in `automations.yaml` / `scripts.yaml` / `packages/*.yaml`
  ändern (Entity-ID bleibt). Danach `check_config` + `automation.reload` / `script.reload`.
- **Icon/Labels/Kategorie/Anzeigename:** WebSocket
  `config/entity_registry/update` mit `entity_id` + `icon` / `labels` /
  `categories` / `name`.
- **Labels:** `config/label_registry/create|update` (Create vergibt die ID selbst;
  Farbe + `mdi:`-Icon mitgeben).
- **Kategorien:** `config/category_registry/create|update`, `scope` ist Pflicht
  (`automation` | `script` | `helpers`); `categories` im Entity-Update ist ein
  Dict `scope → category_id`.
- **Helfer** sind UI-managed (`.storage`) und editierbar; neue Helfer über die UI
  bzw. den Config-Flow anlegen, nicht mehr in `configuration.yaml`.
- Prüfen: `docker exec homeassistant python -m homeassistant --script check_config -c /config`.
- Commit im `/etc/docker`-Repo: `homeassistant: <was>`.
