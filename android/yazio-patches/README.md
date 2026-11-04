# YAZIO Patches

Morphe-Patch-Bundle für **YAZIO** (`com.yazio.android`).

Die Patches verändern die App auf dem Gerät: Morphe wendet sie beim Patchen an,
das Ergebnis ist ein selbst signiertes APK. Es wird nichts an fremden Servern
verändert und kein App-Code weitergegeben.

## Patches

| Patch | Typ | Standard | Wirkung |
|---|---|---|---|
| `Trust user certificates` | Resource | aus | Ergänzt `res/xml/network_security_config.xml` um die Vertrauensanker `system` und `user`, damit die App TLS-Verbindungen über einen Proxy mit eigenem CA-Zertifikat (z. B. mitmproxy) akzeptiert. |

Der Patch ist ein reiner Resource-Patch: er ändert kein Smali und keine
Dex-Dateien, sondern nur eine XML-Ressource. Dadurch überlebt er App-Updates,
solange die Datei existiert.

## Benutzung

1. **Morphe** installieren: <https://morphe.software> (oder die Releases von
   [MorpheApp/morphe-manager](https://github.com/MorpheApp/morphe-manager)).
2. Morphe → **Sources** → **+** → Reiter **Local** → `patches-*.mpp` auswählen.
   Sobald das Projekt unter `github.com/DasCarstel/yazio-patches` veröffentlicht
   ist, kann statt der Datei auch diese Adresse im Reiter **Remote** stehen —
   dann aktualisiert sich die Quelle selbst.
3. YAZIO patchen: **Expert mode** einschalten, Patch `Trust user certificates`
   anhaken, APK wählen (`.apkm` von APKMirror funktioniert, Morphe merged die
   Splits selbst).
4. Installieren. Achtung: die Play-Store-Version muss vorher deinstalliert
   werden, weil die Signatur abweicht. Danach sind alle weiteren Patch-Builds
   normale Updates — solange Morphe denselben Keystore benutzt
   (siehe `docs/backup-and-keystore` in der Morphe-Dokumentation).

## Selbst bauen

Voraussetzungen: JDK 21, ein GitHub-Token mit `read:packages` (Morphe
veröffentlicht den Patcher ausschließlich über GitHub Packages).

```sh
export GITHUB_TOKEN=...
./gradlew :patches:build -Pgpr.user=<github-user> -Pgpr.key="$GITHUB_TOKEN"
# Ergebnis: patches/build/libs/patches-<version>.mpp
```

## Patch gegen eine APK testen

Das Modul `harness` wendet ein Bundle auf eine APK an, ohne Morphe:

```sh
./gradlew :harness:run -Pgpr.user=<user> -Pgpr.key="$GITHUB_TOKEN" \
  --args="/pfad/app.apk /pfad/patches-1.0.0.mpp /tmp/patchtest"
```

Das Ergebnis liegt in `/tmp/patchtest/resources-patched.apk`; die geänderte
Ressource lässt sich z. B. mit
`aapt2 dump xmltree --file res/xml/network_security_config.xml <apk>` prüfen.

## Struktur

```
patches/src/main/kotlin/app/yaziopatches/patches/
  shared/Constants.kt              # App-Kompatibilität (Paket, Versionen)
  trustusercerts/…Patch.kt         # Patch-Implementierung
harness/                           # lokales Testwerkzeug
```

## Lizenz

GPLv3, siehe [LICENSE](LICENSE). Basiert auf
[MorpheApp/morphe-patches-template](https://github.com/MorpheApp/morphe-patches-template).
