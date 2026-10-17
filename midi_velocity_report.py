#!/usr/bin/env python3
"""Read-only MIDI 1.0 positive Note-On velocity distribution.

Mido decodes MIDI events. Only SMF container boundaries are checked here.
No playing, ports, networking, MIDI rewrite, hand assignment or octave folding.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import io
import json
import os
import stat
import struct
import sys

MAX_BYTES = 8 * 1024 * 1024
MAX_TRACK_BYTES = 64 * 1024
MAX_TRACKS = 256
MAX_TRACK_MESSAGES = 25_000
MAX_MESSAGES = 200_000
MAX_NOTE_EVENTS = 100_000
MAX_TICK = (1 << 53) - 1
PITCH_CLASSES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


class VelocityReportError(ValueError):
    """Readable input or option failure; no partial JSON should be emitted."""


def note_label(note: int) -> str:
    if type(note) is not int or not 0 <= note <= 127:
        raise VelocityReportError("MIDI-Notennummer muss zwischen 0 und 127 liegen.")
    return f"{PITCH_CLASSES[note % 12]}{note // 12 - 1}"


def _note_reference(note: int) -> dict:
    return {"midi_note": note, "label": note_label(note)}


def _bounded_tracks(data: bytes) -> tuple[int, int, list[bytes]]:
    """Validate container framing, not MIDI event encoding."""
    if len(data) > MAX_BYTES:
        raise VelocityReportError("Datei überschreitet 8 MiB.")
    if len(data) < 14 or data[:4] != b"MThd":
        raise VelocityReportError("Ein vollständiger Standard-MIDI-Header fehlt.")
    if struct.unpack(">I", data[4:8])[0] != 6:
        raise VelocityReportError("Nur ein Standard-MIDI-Header mit sechs Nutzbytes wird unterstützt.")
    kind, count, division = struct.unpack(">HHH", data[8:14])
    if kind not in (0, 1):
        raise VelocityReportError("Unterstützt werden SMF Typ 0/1; asynchroner Typ 2 ist ausgeschlossen.")
    if not 1 <= count <= MAX_TRACKS:
        raise VelocityReportError(f"Unterstützt werden 1 bis {MAX_TRACKS} Spuren.")
    if kind == 0 and count != 1:
        raise VelocityReportError("SMF Typ 0 muss genau eine Spur enthalten.")
    if division == 0 or division & 0x8000:
        raise VelocityReportError("Unterstützt wird positive PPQN-Zeitteilung, kein SMPTE.")
    offset = 14
    tracks = []
    for index in range(count):
        if offset + 8 > len(data) or data[offset:offset + 4] != b"MTrk":
            raise VelocityReportError(f"Spur {index}: vollständiger MTrk-Block fehlt.")
        length = struct.unpack(">I", data[offset + 4:offset + 8])[0]
        if length > MAX_TRACK_BYTES:
            raise VelocityReportError(f"Spur {index} überschreitet 64 KiB Rohdaten.")
        start, end = offset + 8, offset + 8 + length
        if end > len(data):
            raise VelocityReportError(f"Spur {index}: Chunk-Länge reicht über das Dateiende hinaus.")
        tracks.append(data[start:end])
        offset = end
    if offset != len(data):
        raise VelocityReportError("Zusätzliche nicht deklarierte Bytes oder Blöcke am Dateiende.")
    return kind, division, tracks


def _read_notes(data: bytes) -> dict:
    kind, division, chunks = _bounded_tracks(data)
    try:
        import mido
    except ImportError as exc:
        raise VelocityReportError("Mido fehlt; bitte requirements.txt in einer virtuellen Umgebung installieren.") from exc
    notes = []
    tracks = []
    counts = Counter()
    for track_index, chunk in enumerate(chunks):
        framed = struct.pack(">4sIHHH4sI", b"MThd", 6, 0, 1, division, b"MTrk", len(chunk)) + chunk
        try:
            track = mido.MidiFile(file=io.BytesIO(framed), clip=False).tracks[0]
        except (EOFError, OSError, ValueError, TypeError, IndexError, KeyError, struct.error, mido.KeySignatureError) as exc:
            raise VelocityReportError(f"Spur {track_index}: ungültige MIDI-Daten ({type(exc).__name__}).") from exc
        if len(track) > MAX_TRACK_MESSAGES:
            raise VelocityReportError(f"Spur {track_index} überschreitet {MAX_TRACK_MESSAGES} Nachrichten.")
        counts["messages"] += len(track)
        if counts["messages"] > MAX_MESSAGES:
            raise VelocityReportError(f"Datei überschreitet {MAX_MESSAGES} Nachrichten insgesamt.")
        if not track or track[-1].type != "end_of_track" or any(message.type == "end_of_track" for message in track[:-1]):
            raise VelocityReportError(f"Spur {track_index}: genau eine abschließende End-of-Track-Nachricht wird benötigt.")
        tick = 0
        track_notes = 0
        for message_index, message in enumerate(track):
            tick += message.time
            if tick > MAX_TICK:
                raise VelocityReportError("Absolute Ticks überschreiten den sicheren JSON-Ganzzahlbereich.")
            if message.type == "midi_port":
                raise VelocityReportError("MIDI-Port-Metadaten werden nicht unterstützt; Kanalgruppen wären ohne Portmodell irreführend.")
            if message.type == "note_on" and message.velocity > 0:
                notes.append({"midi_note": message.note, "channel": message.channel + 1,
                              "track_index": track_index, "message_index": message_index, "absolute_tick": tick, "velocity": message.velocity})
                track_notes += 1
                if len(notes) > MAX_NOTE_EVENTS:
                    raise VelocityReportError(f"Datei überschreitet {MAX_NOTE_EVENTS} positive Note-On-Ereignisse.")
            elif message.type == "note_on":
                counts["zero_velocity_note_on"] += 1
            elif message.type == "note_off":
                counts["note_off"] += 1
            elif message.type == "pitchwheel":
                counts["pitchwheel"] += 1
            elif message.type == "sysex":
                counts["sysex"] += 1
        tracks.append({"track_index": track_index, "message_count": len(track), "positive_note_on_count": track_notes, "end_tick": tick})
        del track
    notes.sort(key=lambda event: (event["absolute_tick"], event["track_index"], event["message_index"]))
    return {"smf_type": kind, "ticks_per_quarter_note": division, "tracks": tracks, "counts": counts, "notes": notes}


def _int_option(value: int, name: str, low: int, high: int) -> None:
    if type(value) is not int or not low <= value <= high:
        raise VelocityReportError(f"{name} muss eine ganze Zahl zwischen {low} und {high} sein.")


def _selection(values, name: str, low: int, high: int) -> list[int] | None:
    if values is None:
        return None
    if not isinstance(values, (list, tuple, set)) or not values:
        raise VelocityReportError(f"{name} muss eine nicht leere Liste ganzer Zahlen sein.")
    for value in values:
        _int_option(value, name, low, high)
    if len(values) != len(set(values)):
        raise VelocityReportError(f"{name} enthält doppelte Einträge.")
    return sorted(values)


def _summary(events: list[dict]) -> dict:
    """Unweighted event statistics; no interpolation or loudness model."""
    values = sorted(event['velocity'] for event in events)
    n = len(values)
    histogram = Counter(values)
    return {'note_on_count': n, 'minimum': values[0] if n else None,
            'maximum': values[-1] if n else None,
            'mean': round(sum(values) / n, 6) if n else None,
            'median': ((values[(n - 1) // 2] + values[n // 2]) / 2) if n else None,
            'distinct_values': len(histogram),
            'velocity_histogram': [{'velocity': value, 'count': histogram[value]} for value in sorted(histogram)]}


def analyze_bytes(data: bytes, *, channels=None, tracks=None, notes=None) -> dict:
    """Describe encoded velocities without diagnosing performance or hardware."""
    cs = _selection(channels, 'Kanalauswahl', 1, 16)
    ts = _selection(tracks, 'Spurauswahl', 0, MAX_TRACKS - 1)
    ns = _selection(notes, 'Notenauswahl', 0, 127)
    raw = _read_notes(data)
    track_count = len(raw['tracks'])
    if ts is not None and any(index >= track_count for index in ts):
        raise VelocityReportError('Eine ausgewählte Spur existiert in dieser Datei nicht.')
    channel_set = set(cs if cs is not None else range(1, 17))
    track_set = set(ts if ts is not None else range(track_count))
    note_set = set(ns if ns is not None else range(128))
    selected = [e for e in raw['notes'] if e['channel'] in channel_set and e['track_index'] in track_set and e['midi_note'] in note_set]
    by_channel, by_track, by_note = defaultdict(list), defaultdict(list), defaultdict(list)
    for e in selected:
        by_channel[e['channel']].append(e)
        by_track[e['track_index']].append(e)
        by_note[e['midi_note']].append(e)
    return {
        'schema_version': 1,
        'input': {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()},
        'scope': 'Encoded MIDI1.0 positive Note-On velocities; no acoustic, force, timing-quality or hardware diagnosis.',
        'smf_type': raw['smf_type'], 'ticks_per_quarter_note': raw['ticks_per_quarter_note'],
        'selection': {'tracks_zero_based': sorted(track_set), 'channels_one_based': sorted(channel_set),
                      'notes': sorted(note_set) if ns is not None else None,
                      'positive_note_on_count_in_file': len(raw['notes']),
                      'selected_note_on_count': len(selected),
                      'excluded_positive_note_on_count': len(raw['notes']) - len(selected)},
        'observed': _summary(selected),
        'tracks': [{**t, 'selected': _summary(by_track[t['track_index']])} for t in raw['tracks']],
        'channels': [{'channel': k, **_summary(by_channel[k])} for k in sorted(by_channel)],
        'notes': [{**_note_reference(k), **_summary(by_note[k])} for k in sorted(by_note)],
        'message_counts': {k: raw['counts'][k] for k in ['messages', 'zero_velocity_note_on', 'note_off', 'pitchwheel', 'sysex']},
        'warnings': (["Kanal10 ist mitgezählt; die musikalische Rolle wird nicht aus der Kanalnummer abgeleitet."] if 10 in by_channel else []) + [
            'Jeder positive Note On zählt einmal. Lange Noten erhalten kein größeres Gewicht; Note On mit Velocity0 und Note Off sind keine Anschläge in dieser Statistik.',
            'Velocity ist hier ein gespeicherter Zahlenwert, kein gemessener Schalldruck oder mechanischer Kraftwert.',
            'Anschlagkurven, nachträgliche MIDI-Bearbeitung, Klangprogramme, Controller und Aftertouch werden nicht ausgewertet.',
            'Gleiche oder unterschiedliche Verteilungen beweisen weder gleiche Spielweise noch einen Defekt. Es gibt keinen Sollbereich und keine automatische Reparaturempfehlung.',
            'Notennamen: MIDI60=C4, internationale Kreuznamen; B entspricht deutschem H. Maßgeblich bleibt die MIDI-Nummer.',
            'Keine MIDI-Ports, Wiedergabe, Netzwerkverbindung oder Änderung der Eingabedatei. UMP/MIDI2.0 und MIDI-Port-Metadaten sind nicht unterstützt.']}


def analyze_file(path, **options) -> dict:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(descriptor, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise VelocityReportError('Eingabe muss eine reguläre Datei sein; kein Gerät, FIFO oder Verzeichnis.')
            if info.st_size > MAX_BYTES:
                raise VelocityReportError('Datei überschreitet 8 MiB.')
            data = source.read(MAX_BYTES + 1)
    except VelocityReportError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise VelocityReportError(f'Datei kann nicht gelesen werden ({type(exc).__name__}).') from exc
    return analyze_bytes(data, **options)


def _cli_list(value: str) -> list[int]:
    try:
        if len(value) > 1024 or not value:
            raise ValueError
        return [int(part) for part in value.split(',')]
    except ValueError as exc:
        raise argparse.ArgumentTypeError('Kommagetrennte ganze Zahlen erwartet, z.B.1,2.') from exc


def main(argv=None) -> int:
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(description='Lokale Note-On-Velocity-Verteilung aus MIDI1.0-Dateien; keine Hardwarediagnose.')
    parser.add_argument('midi_file')
    parser.add_argument('--channels', type=_cli_list, help='Kanäle1..16; standardmäßig alle.')
    parser.add_argument('--tracks', type=_cli_list, help='Spurindizes ab0; standardmäßig alle.')
    parser.add_argument('--notes', type=_cli_list, help='MIDI-Notennummern0..127; standardmäßig alle.')
    args = parser.parse_args(argv)
    if sys.version_info < (3, 10):
        print('Fehler: Python3.10 oder neuer wird benötigt.', file=sys.stderr)
        return 2
    try:
        report = analyze_file(args.midi_file, channels=args.channels, tracks=args.tracks, notes=args.notes)
    except VelocityReportError as exc:
        print(f'Fehler: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())


