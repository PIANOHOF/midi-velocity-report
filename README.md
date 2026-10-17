# MIDI Velocity Report

Welche Anschlagswerte stehen tatsächlich in einem MIDI-Export? Dieses lokale Python-Werkzeug zählt positive **Note-On-Velocity-Werte** und zeigt ihre Verteilung insgesamt sowie getrennt nach Notennummer, Kanal und Spur. Es hilft beim Nachvollziehen eines Exports oder beim Vergleich bewusst gleich aufgezeichneter Versuche. Es bewertet weder das musikalische Können noch den Zustand einer Tastatur.

## Start und nachvollziehbares Beispiel

Benötigt werden **Python 3.10+** und **Mido 1.3.3**. Im Projektordner eine virtuelle Umgebung erstellen und aktivieren:

```sh
python3.10 -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# Windows PowerShell stattdessen:
# .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python midi_velocity_report.py demo.mid --channels 1
python -m unittest -v
```

Die Installation benötigt einen Paketdownload; der Bericht selbst entsteht offline. Das Programm öffnet keine MIDI-Ports, erzeugt keinen Ton, überträgt keine Daten und verändert keine MIDI-Datei. JSON erscheint auf stdout. Erfolg liefert Exitstatus **0**; ungültige Eingaben oder Optionen liefern **2**, eine Erklärung auf stderr und keinen Teilbericht.

`demo.mid` enthält ausschließlich eigens erzeugte Testereignisse. Auf Kanal 1 stehen sechs Anschläge derselben Notennummer 60 mit den Werten **24, 48, 48, 72, 96 und 120**. Der Bericht ergibt:

| Velocity | Zahl der Anschläge |
| ---: | ---: |
| 24 | 1 |
| 48 | 2 |
| 72 | 1 |
| 96 | 1 |
| 120 | 1 |

Minimum und Maximum sind 24 und 120, der Mittelwert ist 68, der Median 60. Bei sechs Werten mittelt der Median die beiden mittleren Zahlen 48 und 72; er muss deshalb nicht selbst in der Datei vorkommen. `example-report.json` enthält die vollständige erwartete Ausgabe. Kanal 2 und Kanal 10 enthalten weitere Testanschläge, die `--channels 1` ausschließt.

## Auswahl vor dem Vergleich festlegen

Standardmäßig werden alle Spuren, Kanäle und Notennummern berücksichtigt. `--channels 1,2` wählt Kanäle **1 bis 16**, `--tracks 0,2` die Spurindizes **ab 0**. `--notes 60,62` begrenzt die Statistik auf diese MIDI-Notennummern. Die Filter gelten gemeinsam: Ein Ereignis muss zu jeder gewählten Gruppe passen. Eine Spur kann mehrere Kanäle enthalten, derselbe Kanal in mehreren Spuren vorkommen.

Kanal 10 wird nicht automatisch entfernt oder als Schlagzeug erkannt. Wähle die tatsächlich gemeinten Ereignisse anhand deines Exports. Für getrennte Vergleichsversuche beide Dateien mit denselben Filtern auswerten. Anzahl und Auswahl gehören zum Ergebnis: Zwei Mittelwerte aus unterschiedlich zusammengesetzten Stimmen sind ohne diesen Kontext wenig aussagekräftig.

## Was die Zahlen bedeuten

Jeder Note On mit Velocity **1 bis 127** zählt einmal. Ein Note On mit Velocity 0 steht bei MIDI 1.0 für Note Off und wird ebenso wie eine ausdrückliche Note-Off-Nachricht nicht als Anschlag gezählt. Release-Velocity gehört nicht in diese Verteilung. Eine lange Note erhält kein größeres Gewicht als eine kurze; wiederholte Anschläge werden jeweils gezählt.

Der Bericht enthält Anzahl, Minimum, Maximum, Mittelwert, Median und das Histogramm der tatsächlich vorkommenden Werte. Der Mittelwert wird auf sechs Nachkommastellen gerundet. Eine leere Auswahl liefert Anzahl 0, leere Gruppen und **null** für Minimum, Maximum, Mittelwert und Median. Eine fehlende Beobachtung wird dadurch nicht als Velocity 0 dargestellt. Notennamen verwenden 60 = C4 und internationale Kreuznamen; B entspricht deutschem H. Maßgeblich bleibt die MIDI-Nummer.

Velocity ist keine Messung des Schalldrucks und kein mechanischer Kraftwert. Anschlagkurven und nachträgliche Bearbeitung können die gespeicherten Zahlen verändern; Klangprogramm, Lautstärke-Controller, Expression und Aftertouch werden hier nicht interpretiert. Ein enger Wertebereich beweist deshalb weder einen Defekt noch eine bestimmte Spielweise. Das Werkzeug setzt keinen Sollbereich und vergibt keine Bewertung.

## Grenzen, Herkunft und Prüfung

Unterstützt sind Standard-MIDI-Dateien Typ 0/1 mit positiver PPQN-Zeitteilung. Typ 2, SMPTE, UMP/MIDI 2.0 und MIDI-Port-Metadaten sind ausgeschlossen. Bewusste Obergrenzen: **8 MiB**, **256 Spuren**, **64 KiB Rohdaten und 25.000 Nachrichten je Spur**, insgesamt **200.000 Nachrichten und 100.000 positive Note Ons**. Auch gültige große Exporte können daran scheitern. Mido dekodiert jeweils einen begrenzten Track; dies ist kein vollständiger MIDI-Konformitätsvalidator.

Unabhängig konstruierte synthetische MIDI-Bytes und statistische Vergleichsrechnungen prüfen Filter, Running Status, Velocity 0, leere Ergebnisse, Fehler und Schreibfreiheit. Hardware und Kundenaufnahmen wurden nicht getestet. Code und Anleitung entstanden **mit KI-Unterstützung**. Die begrenzte SMF-Eingabelogik basiert auf dem eigenen MIDI Range Planner; diese Anwendung ergänzt eine eigenständige Velocity-Auswertung. Projektcode steht unter MIT, Mido unter seiner eigenen Lizenz.

Für eine [E-Piano-Reparatur bei PIANOHOF](https://www.pianohof.de/leistungen/e-piano-reparatur) bleiben Modell, tatsächlich beobachtetes Verhalten und Aufnahmebedingungen entscheidend. Eine Dateistatistik ergänzt diese Angaben; sie ersetzt keine Untersuchung von Instrument und Signalweg.

Technische Grundlagen: [Mido – Standard MIDI Files](https://mido.readthedocs.io/en/stable/files/midi.html), [MIDI Association – Message List](https://midi.org/expanded-midi-1-0-messages-list), [Microsoft – MIDI-1.0-Velocity 0](https://microsoft.github.io/MIDI/kb/ump-data-format-primer/).
