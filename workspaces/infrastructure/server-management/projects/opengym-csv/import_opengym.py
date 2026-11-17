#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""import_opengym.py – pushet Trainingshistorie, Routinen und Wochenplan in eine
selbst gehostete openGym-Instanz (scoped-token API).

Nur stdlib (python3.12). Läuft auch auf einem nackten Server.

CLI:
    python3 import_opengym.py --dry-run   # Standard: nichts anfassen, alles zeigen
    python3 import_opengym.py --apply     # GET → mergen → PUT (409-sicher, max 2 Retries)
    python3 import_opengym.py --mapping   # nur die Name→Id-Tabelle als Markdown

Eingaben (alles unter /tmp/opencode/opengym/):
    bundle.json   {csv, entries, exNames, workoutNames}   (aus build_csv.py + Obsidian-Frontmatter)
    de.js         deutsche Übungsnamen der App: export default JSON.parse('{"1000":"...", ...}')
    state.json    aktuelle GET-/api/token/data-Antwort {state, rev, wid} (nur als Referenz;

Schema/Verifizierung (openGym v1.4.5, repo sha e4a68a9):
    - Warmup-Marker: import-csv.js markiert Sätze aus `set type`-Zellen, auf die /warm/i passt,
      mit `phase: 'warmup'` (import-csv.js L760/L841); isWarmupRow prüft genau darauf
      (workout-model.js L10-L23). Legacy `warmup: true` existiert noch, geschrieben wird `phase`.
    - Kein Zeit-Spalten im CSV: start = lokales Mitternacht des Tages + 18h, end = start
      (import-csv.js L997-L1001: `base = new Date(d+'T00:00:00').getTime()`,
      `start = base + (day.start ?? 18*3600000)`).
    - Ids: importId() = cyrb53, base36, Präfix 'iw' (Workout) / 'im' (customEx) – hier 1:1 portiert.
    - week: {"0".."6": routineId|"rest"}, 0 = SONNTAG, 1 = Montag … 6 = Samstag.
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

BASE_DIR = '/tmp/opencode/opengym'
BUNDLE = os.path.join(BASE_DIR, 'bundle.json')
DE_JS = os.path.join(BASE_DIR, 'de.js')
STATE = os.path.join(BASE_DIR, 'state.json')
DEFAULT_TOKEN_FILE = os.path.join(BASE_DIR, 'token')

BASE_URL = 'https://gym.mueller-nas.de'

# Stabile Ids, damit --apply mehrfach laufen kann (App vereinigt nach Id).
IMPORT_SOURCE = 'opengym-import'

# Woche: 1 = Mo, 3 = Mi, 5 = Fr (in der App anpassbar)
WEEK_PLAN = {'1': 'r_push', '3': 'r_pull', '5': 'r_legs'}
ROUTINES = [
    ('r_push', 'Push'),
    ('r_pull', 'Pull'),
    ('r_legs', 'Beine und Bauch'),
]

# ---------------------------------------------------------------- exercise map
# Von Hand geprüft gegen die deutschen Namen in de.js (regel 3: semantisch richtig,
# nicht nur wortbaggy). Fallback: dynamischer Matcher (wie der App-Importer).
NAME_MAP = {
    # name                          -> id    (deutscher Name in de.js)
    'Squat':                          '1555',  # Kniebeuge mit Kurzhanteln v. 2 (keine Hantel/Rack -> Kurzhantel)
    'Beinbeuger':                     '0339',  # Beinbeugen liegend mit Kurzhantel (Home-Gym: keine Maschine)
    'Beinstrecker':                   '0926',  # Stehendes Beinstrecken mit Band (Home-Gym: Band statt Maschine)
    'Crunch':                         '0831',  # Crunch mit Zusatzgewicht (Knie gebeugt)
    'Dumbbell Flys':                  '0308',  # Fliegende mit Kurzhantel
    'Dumbell Flys':                   '0308',  # Typo, gleiche Übung
    'Dumbbell Hammer Curl':           '2485',  # Hammer-Curl mit Kurzhantel v. 3
    'Dumbbell Pullover':              '5330',  # Pullover mit Kurzhantel v. 2
    'Incline Dumbbell Curl':          '0315',  # Bizeps-Curl auf der Schrägbank mit Kurzhantel
    'Incline Dumbbell Row':           '3320',  # Beidarmiges Rudern an der Schrägbank mit Hammergriff und Kurzhanteln
    'Incline bench press':            '0324',  # Schrägbankdrücken mit Handflächen nach innen und Kurzhanteln (keine Langhantel)
    'Prone Incline Dumbbell Reverse Fly': '2500',  # Reverse Fly auf der Schrägbank mit Kurzhantel
    'Romanian Deadlift':              '1459',  # Rumänisches Kreuzheben mit Kurzhanteln
    'Single-Arm Dumbbell Row':        '2393',  # Einarmiges Rudern vorgebeugt mit Kurzhantel
    'Triceps kickbacks':              '1742',  # Trizeps-Kickback im Reiherstand mit Kurzhantel
    'Triceps skullcrusher':           '4151',  # Skullcrusher liegend am Boden mit Kurzhanteln
    'Wadenübung':                     '0417',  # Wadenheben stehend mit Kurzhantel
    'dumbbell shoulder press':        '0405',  # Sitzendes Schulterdrücken mit Kurzhantel
    'flat chest press':               '0289',  # Bankdrücken mit Kurzhanteln
    'lateral raise':                  '2234',  # Seitheben mit Kurzhantel stehend
    # Nur in den Plan-Einträgen (nicht im CSV):
    'Beine hoch':                     '0620',  # Liegendes Beinheben auf der Flachbank (Home-Gym: FID-Bank statt Stange)
    'Planks':                         '0463',  # Unterarmstütz
}

# ---------------------------------------------------------------- de.js laden

def load_de():
    """de.js -> {id: deutscher Name}. Der JS-String-Literal (einzeilig) enthält
    vor allem \\' für Apostrophe; erst JS-Unescape, dann json.loads."""
    src = open(DE_JS, encoding='utf-8').read()
    m = re.search(r"JSON\.parse\('(.*)'\)", src, re.S)
    if not m:
        sys.exit('de.js: JSON.parse(...) nicht gefunden')
    s = m.group(1)
    out, i = [], 0
    while i < len(s):  # JS-Escapes im String-Literal auflösen
        if s[i] == '\\' and i + 1 < len(s):
            out.append(s[i + 1])
            i += 2
        else:
            out.append(s[i])
            i += 1
    return json.loads(''.join(out))


def de_umlaut(s):
    """Zweite Lookup-Variante: ä→ae, ö→oe, ü→ue, ß→ss."""
    return (s.replace('ä', 'ae').replace('ö', 'oe').replace('ü', 'ue')
             .replace('ß', 'ss').replace('Ä', 'Ae').replace('Ö', 'Oe')
             .replace('Ü', 'Ue'))

# ---------------------------------------------------------------- matcher
# Wort-Normalisierung exakt wie der App-Importer (import-csv.js L171-194).
SYN = [
    (r'\bbb\b', 'barbell'), (r'\bdb\b', 'dumbbell'), (r'\bkb\b', 'kettlebell'),
    (r'\bohp\b', 'overhead press'), (r'\bbw\b', 'body weight'), (r'\bbodyweight\b', 'body weight'),
    (r'\bsmith machine\b', 'smith'), (r'\bmachine\b', 'lever'), (r'\bez bar\b', 'ez barbell'),
    (r'\bpull ups?\b', 'pull up'), (r'\bchin ups?\b', 'chin up'), (r'\bpush ups?\b', 'push up'),
    (r'\bsit ups?\b', 'sit up'), (r'\bdips?\b', 'dip'), (r'\braises?\b', 'raise'),
    (r'\bcurls?\b', 'curl'), (r'\bpresses\b', 'press'), (r'\bextensions?\b', 'extension'),
    (r'\bcables?\b', 'cable'), (r'\bseated\b', 'seated'), (r'\bassisted\b', 'assisted'),
]
FILLER = {'the', 'a', 'with', 'and', 'v', 'variation', 'version', 'pulley', 'weighted'}


def words_of(name):
    k = str(name or '').lower().replace('(', ' ').replace(')', ' ').replace('[', ' ').replace(']', ' ')
    k = re.sub(r'[^a-z0-9]+', ' ', k).strip()
    for pat, to in SYN:
        k = re.sub(pat, to, k)
    return [w for w in k.split(' ') if w and w not in FILLER]


class ExIndex:
    """norm(deutscher Name) -> id; zwei Pässe (mit/ohne Umlaute) + Wortbeutel-Enthaltensein."""

    def __init__(self, de):
        self.exact = {}
        self.all = []
        for ident, name in de.items():
            for variant in (name, de_umlaut(name)):
                w = words_of(variant)
                if not w:
                    continue
                key = ' '.join(sorted(w))
                self.exact.setdefault(key, ident)
                self.all.append({'id': ident, 'set': frozenset(w), 'n': len(w), 'key': key})
        self.byid = de

    def match(self, name):
        """(id | None, grund) – Reihenfolge wie matchExercise: exakt, dann Enthaltensein
        (nur bei genau einem Kandidaten mit ≤2 Extra-Wörtern)."""
        w = words_of(name)
        if not w:
            return None, 'leer'
        variants = [name, de_umlaut(name)]
        for v in variants:
            wv = words_of(v)
            exact = self.exact.get(' '.join(sorted(wv)))
            if exact:
                return exact, 'exakt'
        for v in variants:
            q = set(words_of(v))
            best, best_extra, ties = None, None, 0
            for c in self.all:
                if not q <= c['set']:
                    continue
                extra = c['n'] - len(q)
                if extra > 2:
                    continue
                if best_extra is None or extra < best_extra:
                    best, best_extra, ties = c['id'], extra, 1
                elif extra == best_extra:
                    ties += 1
            if ties == 1 and best:
                return best, 'wortbeutel'
        return None, 'keine-uebereinstimmung'


# ---------------------------------------------------------------- ids (cyrb53-Port von importId)

def import_id(prefix, text):
    h1, h2 = 0xdeadbeef, 0x41c6ce57
    for ch in text:
        c = ord(ch)
        h1 = ((h1 ^ c) * 2654435761) & 0xffffffff
        h2 = ((h2 ^ c) * 1597334677) & 0xffffffff
    def imul(a, b):
        return (a * b) & 0xffffffff
    h1 = imul(h1 ^ (h1 >> 16), 2246822507) ^ imul(h2 ^ (h2 >> 13), 3266489909)
    h2 = imul(h2 ^ (h2 >> 16), 2246822507) ^ imul(h1 ^ (h1 >> 13), 3266489909)
    return prefix + _b36(4294967296 * (2097151 & h2) + (h1 & 0xffffffff))


def _b36(n):
    """int -> base36 (wie JS .toString(36)), für n >= 0."""
    if n == 0:
        return '0'
    digits = '0123456789abcdefghijklmnopqrstuvwxyz'
    out = ''
    while n:
        out = digits[n % 36] + out
        n //= 36
    return out


def workout_id(d, name, entries):
    """Identisch zum App-Import: importId('iw', JSON.stringify([source, d, start, name, entries]))."""
    payload = json.dumps([IMPORT_SOURCE, d, _start_ms(d), name or '',
                          [[e['id'], e['sets']] for e in entries]],
                         separators=(',', ':'), ensure_ascii=False)
    return import_id('iw', payload)


def custom_id(name):
    return import_id('im', IMPORT_SOURCE + '|' + name.lower())

# ---------------------------------------------------------------- Zeit

_BERLIN = None
try:
    from zoneinfo import ZoneInfo
    _BERLIN = ZoneInfo('Europe/Berlin')
except Exception:
    _BERLIN = None


def _start_ms(d, hh=18):
    """ms-Epoch von d hh:00 lokal (Berlin, fallback UTC) – wie die App rechnet:
    Mitternacht des Tages + Offset. Ohne Zeitangabe im CSV: 18h."""
    try:
        base = time.mktime(time.strptime(d, '%Y-%m-%d'))
        if _BERLIN is not None:
            import datetime
            dt = datetime.datetime.strptime(d, '%Y-%m-%d').replace(tzinfo=_BERLIN)
            return int(dt.timestamp() * 1000) + hh * 3600000
        return int(base * 1000) + hh * 3600000
    except Exception:
        return int(time.mktime(time.strptime(d, '%Y-%m-%d')) * 1000) + hh * 3600000

# ---------------------------------------------------------------- Parsing (aus build_csv.py)

def strip_quotes(raw):
    s = str(raw or '').strip()
    while len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        s = s[1:-1].strip()
    return s


def parse_reps(raw):
    """Wiederholungen -> int oder None (Zeit/Halten -> None)."""
    if raw is None:
        return None
    s = strip_quotes(raw)
    if not s:
        return None
    low = s.lower()
    if 'sec' in low or 'halten' in low:
        return None
    m = re.search(r'\[\[(\d+)(?:-(\d+))?\]\]', s)
    if m:
        if m.group(2):
            return (int(m.group(1)) + int(m.group(2))) // 2
        return int(m.group(1))
    tokens = re.findall(r'#(\S+)', s)
    if tokens:
        for t in tokens:
            if t == 'st':
                return 10
            if t == 'kl':
                return 15
            num = re.search(r'\d+', t)
            if num:
                return int(num.group())
        return None
    m = re.fullmatch(r'(\d+)\s*[x×]', s, re.IGNORECASE)
    if m:
        return int(m.group(1))
    if re.fullmatch(r'\d+', s):
        return int(s)
    return None


def parse_weight(raw):
    """Gewicht -> (status, wert|None, rohwert); None = Bodyweight (`0`)."""
    s = strip_quotes(raw)
    if not s:
        return 'skip', None, ''
    low = s.lower()
    if '(weg)' in low or 'halten' in low or '?' in s:
        return 'skip', None, ''
    note = s
    if s.startswith('(') and s.endswith(')'):
        return 'skip', None, ''
    m = re.search(r'\([^)]*\)', s)
    if m and s[:m.start()].strip():
        s = s[:m.start()].strip()
    if s == '0':
        return 'ok', None, note
    m = re.fullmatch(r'(\d+(?:[.,]\d+)?)\s*[x×]\s*(.+)', s, re.IGNORECASE)
    if m:
        s = m.group(2).strip()
    try:
        return 'ok', float(s.replace(',', '.')), note
    except ValueError:
        return 'error', None, ''


def parse_sec(raw):
    """Zeitwert -> Sekunden oder None. '30 sec'/'45 sec' -> Zahl, 'halten' -> 30."""
    s = strip_quotes(raw).lower()
    if not s:
        return None
    m = re.search(r'(\d+)\s*sec', s)
    if m:
        return int(m.group(1))
    if 'halten' in s:
        return 30
    m = re.fullmatch(r'(\d+)', s)
    if m and 'sec' not in s:
        return None
    return None


def is_time_raw(*vals):
    return any('sec' in strip_quotes(v).lower() or 'halten' in strip_quotes(v).lower() for v in vals if v)


def wiki_name(raw):
    """`[[Beinbeuger]]` / `[[Band Lateral Walk|Band Lateral Walk (neu)]]` -> Name."""
    m = re.search(r'\[\[([^\]|]+)', str(raw or ''))
    return m.group(1).strip() if m else ''


def wiki_int(raw):
    s = strip_quotes(raw)
    s = s.replace('[[', '').replace(']]', '').split('|')[0].strip()
    m = re.search(r'-?\d+', s)
    return int(m.group()) if m else None

# ---------------------------------------------------------------- Daten laden

def load_bundle():
    b = json.load(open(BUNDLE, encoding='utf-8'))
    rows = list(csv.DictReader(io.StringIO(b['csv'])))
    return b, rows


def load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE, encoding='utf-8'))
    return None

# ---------------------------------------------------------------- Workouts aus CSV

def build_workouts(rows, resolve):
    """(workouts, summarien) – Gruppe = (Datum, Workoutname); Entry = fortlaufende
    Zeilen derselben Übung; Warmup-Zeilen -> phase:'warmup'."""
    groups = {}
    order = []
    for r in rows:
        key = (r['date'], r['workout name'])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    workouts, summaries = [], []
    for (d, name) in order:
        grp = groups[(d, name)]
        entries, cur = [], None
        for r in grp:
            ex = r['exercise']
            ident = resolve(ex)
            if cur is None or cur['exname'] != ex:
                cur = {'exname': ex, 'id': ident, 'sets': [], 'notes': []}
                entries.append(cur)
            s = {'w': float(r['weight kg'] or 0), 'r': int(r['reps'] or 0), 'done': True}
            if re.search(r'warm', r['set type'] or '', re.I):
                s['phase'] = 'warmup'
            cur['sets'].append(s)
            note = (r['note'] or '').strip()
            if note and note not in cur['notes']:
                cur['notes'].append(note)
        out_entries = []
        for e in entries:
            ent = {'id': e['id'], 'sets': e['sets']}
            if e['notes']:
                ent['note'] = ' · '.join(e['notes'])
            out_entries.append(ent)
        w = {
            'id': workout_id(d, name, out_entries),
            'd': d,
            'start': _start_ms(d, 18),
            'end': _start_ms(d, 18),
            'name': name,
            'routineId': None,
            'entries': out_entries,
        }
        workouts.append(w)
        nsets = sum(len(e['sets']) for e in out_entries)
        nwarm = sum(1 for e in out_entries for s in e['sets'] if s.get('phase') == 'warmup')
        summaries.append((d, name, len(out_entries), nsets, nwarm))
    return workouts, summaries

# ---------------------------------------------------------------- Routinen aus entries

PLAN_MAP = {'Push': ('r_push', 'Push'), 'Pull': ('r_pull', 'Pull'),
            'Beine und Bauch': ('r_legs', 'Beine und Bauch')}


def is_active(status_raw):
    s = strip_quotes(status_raw).lower()
    return (not s) or ('aktiv' in s) or ('active' in s)


def build_routines(entries, resolve):
    """(routinen, report) – nur aktive Einträge, gruppiert nach Plan, sortiert nach Reihenfolge."""
    active, skipped = [], []
    for e in entries:
        name = wiki_name(e.get('Übung', ''))
        if not name:
            skipped.append((e.get('file', '?'), 'keine Übung'))
            continue
        if is_active(e.get('Status', '')):
            active.append(e)
        else:
            skipped.append((name, strip_quotes(e.get('Status', ''))))
    active.sort(key=lambda e: (wiki_int(e.get('Reihenfolge', '')) or 999, wiki_name(e.get('Übung', ''))))

    by_plan = {}
    for e in active:
        plan_raw = strip_quotes(e.get('Plan', ''))
        plan = wiki_name(plan_raw) or plan_raw.split('/')[-1].strip()
        by_plan.setdefault(plan, []).append(e)

    routines, report = [], []
    for rid, rname in ROUTINES:
        ex_list = []
        for e in by_plan.get(rname, []):
            name = wiki_name(e.get('Übung', ''))
            wdh_fields = [e.get(k, '') for k in ('A_Wdh', 'S1_Wdh', 'S2_Wdh', 'S3_Wdh')]
            gew_fields = [e.get(k, '') for k in ('A_Gewicht', 'S1_Gewicht', 'S2_Gewicht', 'S3_Gewicht')]
            note_extra = ''
            if any(strip_quotes(v).lower().find('halten') >= 0 or re.search(r'\d+\s*sec', strip_quotes(v).lower())
                   for v in wdh_fields + gew_fields):
                mode = 'time'
                sec = None
                for v in wdh_fields + gew_fields:
                    sec = sec or parse_sec(v)
                re_ = {'id': resolve(name), 'sets': _sets_count(e), 'mode': 'time',
                       'sec': sec or 30, 'weight': 0}
                if 'halten' in (strip_quotes(e.get('A_Gewicht', '')) + strip_quotes(e.get('A_Wdh', ''))).lower():
                    note_extra = 'Halten'
            else:
                mode = 'reps'
                reps = parse_reps(e.get('A_Wdh', ''))
                wsrc = ''
                for v in wdh_fields:
                    if reps is not None:
                        break
                    reps = parse_reps(v)
                wval, wraw = 0.0, ''
                st, val, raw = parse_weight(e.get('A_Gewicht', ''))
                if st == 'ok':
                    wval, wraw = (val or 0.0), raw
                else:
                    for k in ('S1_Gewicht', 'S2_Gewicht', 'S3_Gewicht'):
                        st, val, raw = parse_weight(e.get(k, ''))
                        if st == 'ok':
                            wval, wraw = (val or 0.0), raw
                            break
                re_ = {'id': resolve(name), 'sets': _sets_count(e), 'mode': 'reps',
                       'reps': reps if reps is not None else 10, 'weight': wval}
                if wraw == '0':
                    re_['bodyweight'] = True
            if strip_quotes(e.get('Beidseitig', '')).lower() in ('true', '1', 'yes'):
                re_['side'] = True
            if note_extra:
                re_['note'] = note_extra
            ex_list.append(re_)
        routine = {'id': rid, 'name': rname, 'prog': 'linear', 'ex': ex_list}
        routines.append(routine)
        report.append((rid, rname, [r['id'] for r in ex_list]))
    return routines, report, skipped


def _sets_count(e):
    """Pflicht-Sätze: höchster vorhandener S*-Index (S1..S3), sonst 3."""
    n = 0
    for i in (1, 2, 3):
        if strip_quotes(e.get('S%d_Wdh' % i, '')) or strip_quotes(e.get('S%d_Gewicht' % i, '')):
            n = i
    return n or 3

# ---------------------------------------------------------------- exWeights

def build_exweights(rows, resolve, entries, today):
    """Letztes Gewicht pro Übung (Prefill der nächsten Session): schwerster
    Nicht-Warmup-Satz am neuesten CSV-Datum; für Plan-Übungen ohne CSV-Historie
    das geparsste A_Gewicht (Fallback S*)."""
    per_ex = {}
    for r in rows:
        per_ex.setdefault(r['exercise'], []).append(r)
    out = {}
    for name, grp in per_ex.items():
        d = max(g['date'] for g in grp)
        ws = [float(g['weight kg'] or 0) for g in grp
              if not re.search(r'warm', g['set type'] or '', re.I)]
        if ws:
            out[resolve(name)] = {'w': max(ws), 'd': d}
    for e in entries:
        if not is_active(e.get('Status', '')):
            continue
        name = wiki_name(e.get('Übung', ''))
        if name in per_ex:
            continue  # CSV-Historie gewinnt
        st, val, raw = parse_weight(e.get('A_Gewicht', ''))
        if st != 'ok':
            for k in ('S1_Gewicht', 'S2_Gewicht', 'S3_Gewicht'):
                st, val, raw = parse_weight(e.get(k, ''))
                if st == 'ok':
                    break
        if st == 'ok' and val is not None:
            out[resolve(name)] = {'w': val, 'd': today}
    return out

# ---------------------------------------------------------------- Netz

def api_get(token, path):
    req = urllib.request.Request(BASE_URL + path,
                                 headers={'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode('utf-8'))


def api_put(token, path, body):
    data = json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(BASE_URL + path, data=data, method='PUT',
                                 headers={'Authorization': 'Bearer ' + token,
                                          'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode('utf-8'))


def read_token(args):
    if os.environ.get('OPENGYM_TOKEN'):
        return os.environ['OPENGYM_TOKEN'].strip()
    path = args.token_file or DEFAULT_TOKEN_FILE
    if not os.path.exists(path):
        sys.exit('Kein Token: --token-file %s fehlt und OPENGYM_TOKEN ist nicht gesetzt' % path)
    return open(path, encoding='utf-8').read().strip()

# ---------------------------------------------------------------- Merge

WRITABLE = ('workouts', 'routines', 'week', 'customEx', 'exWeights', 'bodyweight')


def apply_changes(base_state, planned, existing_days):
    """Vollständigen Scoped-State zurückgeben, nur die schreibbaren Keys verändert."""
    st = json.loads(json.dumps(base_state))  # deep copy, alles unverändert durchreichen
    st['workouts'] = sorted(
        list(st.get('workouts') or []) + [w for w in planned['workouts'] if w['d'] not in existing_days],
        key=lambda w: (w['d'], w.get('start') or 0))
    st['routines'] = planned['routines']
    st['week'] = dict(planned['week'])
    have = {c['id']: c for c in (st.get('customEx') or [])}
    for c in planned['customEx']:
        have.setdefault(c['id'], c)
    st['customEx'] = list(have.values())
    ew = dict(st.get('exWeights') or {})
    ew.update(planned['exWeights'])
    st['exWeights'] = ew
    return st


def do_apply(token, planned, log):
    data = api_get(token, '/api/token/data')
    state, rev, wid = data['state'], data['rev'], data['wid']
    existing_days = {w.get('d') for w in (state.get('workouts') or [])}
    skipped = [w['d'] for w in planned['workouts'] if w['d'] in existing_days]
    if skipped:
        log('  übersprungen (Tag existiert schon): ' + ', '.join(sorted(set(skipped))))
    for attempt in range(1, 3):
        st = apply_changes(state, planned, existing_days)
        code, resp = api_put(token, '/api/token/data',
                             {'state': st, 'baseRev': rev, 'baseWid': wid})
        if code == 200 and resp.get('ok'):
            return resp, skipped
        if code == 409 and resp.get('error') == 'conflict':
            log('  409 conflict (rev %s) – Re-Merge auf neuen Stand, Retry %d/2' % (resp.get('rev'), attempt))
            state, rev, wid = resp['state'], resp['rev'], resp['wid']
            existing_days = {w.get('d') for w in (state.get('workouts') or [])}
            continue
        sys.exit('PUT fehlgeschlagen: HTTP %s %s' % (code, resp))
    sys.exit('PUT auch nach 2 Retries nicht erfolgreich (Konflikt)')

# ---------------------------------------------------------------- Ausgabe

def mapping_table(names, de, resolve, reason):
    lines = ['| CSV/Plan-Name | library id | deutscher Name | via |',
             '|---|---|---|---|']
    for n in names:
        ident, how = reason(n)
        gname = de.get(ident, '(custom)')
        lines.append('| `%s` | `%s` | %s | %s |' % (n, ident or '—', gname, how))
    return '\n'.join(lines)

# ---------------------------------------------------------------- main

def main():
    global BASE_URL
    ap = argparse.ArgumentParser(description='CSV/Plan-Notizen -> openGym (scoped-token API)')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--dry-run', action='store_true', help='nichts senden, nur zeigen (Standard)')
    g.add_argument('--apply', action='store_true', help='GET -> mergen -> PUT')
    g.add_argument('--mapping', action='store_true', help='nur die Name->Id-Tabelle ausgeben')
    ap.add_argument('--token-file', default=None, help='Datei mit dem ogt_…-Token')
    ap.add_argument('--base-url', default=BASE_URL)
    args = ap.parse_args()
    BASE_URL = args.base_url.rstrip('/')

    bundle, rows = load_bundle()
    de = load_de()
    idx = ExIndex(de)
    state_ref = load_state()

    names = list(bundle['exNames'])
    for e in bundle['entries']:
        n = wiki_name(e.get('Übung', ''))
        if n and n not in names:
            names.append(n)

    custom_created = []
    matched_cache = {}

    def resolve(name):
        if name in matched_cache:
            return matched_cache[name]
        ident = NAME_MAP.get(name)
        how = 'NAME_MAP'
        if ident is None:
            ident, how = idx.match(name)
            if ident is None:
                ident = custom_id(name)
                how = 'CUSTOM'
                if name.lower() not in [c.lower() for c in custom_created]:
                    custom_created.append(name)
        matched_cache[name] = ident
        return ident

    def reason(n):
        # für die Tabelle: dasselbe wie resolve(), aber mit Begründung
        ident = NAME_MAP.get(n)
        if ident:
            return ident, 'NAME_MAP'
        i2, how = idx.match(n)
        if i2:
            return i2, how
        return custom_id(n), 'CUSTOM'

    planned = {}
    planned['workouts'], wsum = build_workouts(rows, resolve)
    planned['routines'], rreport, skipped_entries = build_routines(bundle['entries'], resolve)
    planned['week'] = dict(WEEK_PLAN)
    planned['customEx'] = [
        {'id': custom_id(n), 'n': n.lower(), 'custom': True, 'eq': 'custom',
         'tg': '', 'desc': '', '_ts': int(time.time() * 1000)}
        for n in custom_created]
    today = max((r['date'] for r in rows), default=None)
    planned['exWeights'] = build_exweights(rows, resolve, bundle['entries'], today)

    # IDs, die weder Library noch Custom sind? resolve() garantiert: immer gesetzt.

    if args.mapping:
        print(mapping_table(names, de, resolve, reason))
        return

    # ---- dry-run / apply: Report
    print('== Übungs-Mapping (%d Namen) ==' % len(names))
    print(mapping_table(names, de, resolve, reason))
    if custom_created:
        print('  -> als Custom-Übung angelegt: ' + ', '.join(custom_created))
    print()

    print('== Plan-Einträge: Status ==')
    import collections as _c
    st_count = _c.Counter(strip_quotes(e.get('Status', '')) for e in bundle['entries'])
    for s, n in sorted(st_count.items()):
        print('  %-24r x%d  -> %s' % (s, n, 'aktiv' if is_active(s) else 'NICHT importiert'))
    for name, why in skipped_entries:
        print('  übersprungen: %s (%s)' % (name, why))
    print()

    print('== Workouts aus CSV ==')
    for d, name, nex, nsets, nwarm in wsum:
        print('  %s  %-16s  %2d Übungen, %2d Sätze (%d warmup)  id=%s'
              % (d, name, nex, nsets, nwarm,
                 next(w['id'] for w in planned['workouts'] if w['d'] == d and w['name'] == name)))
    print()

    print('== Routinen ==')
    for rid, rname, ids in rreport:
        print('  %s (%s): %d Einträge' % (rid, rname, len(ids)))
    print('  week: ' + json.dumps(planned['week'], ensure_ascii=False))
    print()

    print('== customEx ==')
    if planned['customEx']:
        for c in planned['customEx']:
            print('  %s  %s' % (c['id'], c['n']))
    else:
        print('  keine (alle Namen mit Bibliothek id gematcht)')
    print()

    print('== exWeights (Prefill) ==')
    for k, v in sorted(planned['exWeights'].items(), key=lambda kv: kv[1]['d']):
        print('  %-6s w=%-6g d=%s  (%s)' % (k, v['w'], v['d'], de.get(k, '?')))
    print()

    sample = next((w for w in planned['workouts'] if w['d'] == max(x[0] for x in wsum)), planned['workouts'][0])
    print('== Beispiel-Workout (vollständig) ==')
    print(json.dumps(sample, ensure_ascii=False, indent=2))
    print()
    print('== Beispiel-Routine (r_push) ==')
    print(json.dumps(next(r for r in planned['routines'] if r['id'] == 'r_push'),
                     ensure_ascii=False, indent=2))
    print()

    if state_ref:
        days = {w.get('d') for w in (state_ref['state'].get('workouts') or [])}
        clash = sorted(days & {w['d'] for w in planned['workouts']})
        print('== State-Referenz == rev=%s wid=%s workouts=%d' % (
            state_ref['rev'], state_ref['wid'], len(state_ref['state'].get('workouts') or [])))
        print('  schon vorhandene Tage: %s%s' % (', '.join(clash) if clash else 'keine',
                                                 ' (beim --apply übersprungen)' if clash else ''))

    if not args.apply:
        print()
        print('dry-run: nichts gesendet. Zum Schreiben: --apply')
        return

    token = read_token(args)
    def log(msg):
        print(msg)
    resp, skipped = do_apply(token, planned, log)
    print()
    print('== APPLY OK ==')
    print('  neue rev: %s   wid: %s' % (resp.get('rev'), resp.get('wid')))
    print('  workouts gepusht: %d (übersprungen: %d)'
          % (len(planned['workouts']) - len(set(skipped)), len(set(skipped))))
    print('  customEx angelegt: %d' % len(planned['customEx']))
    print('  routinen: %s' % ', '.join('%s(%s)' % (r['id'], r['name']) for r in planned['routines']))
    print('  week: %s' % json.dumps(planned['week']))


if __name__ == '__main__':
    main()
