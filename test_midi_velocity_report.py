"""Independent raw SMF fixtures and distribution oracles; no hardware data."""
import contextlib
import io
import json
from pathlib import Path
import random
import statistics
import struct
import tempfile
import unittest
from unittest.mock import patch

import midi_velocity_report as app


def smf(*tracks, kind=None, division=480):
    if kind is None:
        kind = 0 if len(tracks) == 1 else 1
    return struct.pack('>4sIHHH', b'MThd', 6, kind, len(tracks), division) + b''.join(
        struct.pack('>4sI', b'MTrk', len(t)) + t for t in tracks)


EOT = b'\x00\xff\x2f\x00'


def event(channel, note, velocity):
    return bytes([0, 0x90 + channel - 1, note, velocity])


def fixture(values, channel=1, note=60):
    return smf(b''.join(event(channel, note, x) for x in values) + EOT)


class VelocityTests(unittest.TestCase):
    def test_known_statistics(self):
        r=app.analyze_bytes(fixture([24,48,48,72,96,120]))['observed']
        self.assertEqual((r['note_on_count'],r['minimum'],r['maximum'],r['mean'],r['median'],r['distinct_values']), (6,24,120,68.0,60.0,5))
        self.assertEqual(r['velocity_histogram'],[{'velocity':v,'count':n} for v,n in [(24,1),(48,2),(72,1),(96,1),(120,1)]])

    def test_all_velocity_endpoints(self):
        r=app.analyze_bytes(fixture(list(range(1,128))))['observed']
        self.assertEqual((r['minimum'],r['maximum'],r['mean'],r['median'],r['distinct_values']),(1,127,64,64,127))

    def test_zero_velocity_and_release_not_attacks(self):
        data=smf(event(1,60,0)+b'\x00\x80\x3c\x7f'+event(1,60,64)+EOT)
        r=app.analyze_bytes(data)
        self.assertEqual(r['observed']['note_on_count'],1)
        self.assertEqual(r['message_counts']['zero_velocity_note_on'],1)
        self.assertEqual(r['message_counts']['note_off'],1)
        self.assertEqual(r['observed']['mean'],64)

    def test_running_status_and_zero_velocity(self):
        data=smf(event(1,60,20)+b'\x00\x3c\x64\x00\x3c\x00'+EOT)
        r=app.analyze_bytes(data)
        self.assertEqual(r['observed']['mean'],60)
        self.assertEqual(r['observed']['note_on_count'],2)

    def test_track_channel_note_intersection(self):
        a=event(1,60,10)+event(2,60,20)+event(1,61,30)+EOT
        b=event(1,60,90)+event(10,36,127)+EOT
        r=app.analyze_bytes(smf(a,b),channels=[1],tracks=[1],notes=[60])
        self.assertEqual(r['observed']['mean'],90)
        self.assertEqual(r['selection']['excluded_positive_note_on_count'],4)
        self.assertEqual(r['tracks'][0]['selected']['note_on_count'],0)
        self.assertEqual(r['channels'][0]['channel'],1)
        self.assertEqual(r['notes'][0]['midi_note'],60)

    def test_track_is_not_channel(self):
        r=app.analyze_bytes(smf(event(2,60,10)+EOT,event(2,60,90)+EOT))
        self.assertEqual(len(r['channels']),1)
        self.assertEqual(r['channels'][0]['mean'],50)
        self.assertEqual([x['selected']['mean'] for x in r['tracks']],[10,90])

    def test_channel10_counted_without_role_inference(self):
        r=app.analyze_bytes(fixture([64],channel=10))
        self.assertEqual(r['observed']['note_on_count'],1)
        self.assertIn('Kanal10',r['warnings'][0])

    def test_empty_and_empty_selection_nulls(self):
        for r in [app.analyze_bytes(smf(EOT)),app.analyze_bytes(fixture([64]),notes=[61])]:
            self.assertEqual(r['observed'],{'note_on_count':0,'minimum':None,'maximum':None,'mean':None,'median':None,'distinct_values':0,'velocity_histogram':[]})
            self.assertEqual(r['channels'],[])

    def test_randomized_unweighted_oracle(self):
        rng=random.Random(204817)
        for _ in range(200):
            values=[rng.randrange(1,128) for _ in range(rng.randrange(1,300))]
            r=app.analyze_bytes(fixture(values))['observed']
            self.assertEqual(r['median'],statistics.median(values))
            self.assertEqual(r['mean'],round(statistics.mean(values),6))
            self.assertEqual(sum(x['count'] for x in r['velocity_histogram']),len(values))
            self.assertEqual((r['minimum'],r['maximum']),(min(values),max(values)))

    def test_duration_not_weighted(self):
        short=smf(event(1,60,10)+event(1,61,90)+EOT)
        long=smf(event(1,60,10)+b'\x87\x40\x90\x3d\x5a'+EOT)
        self.assertEqual(app.analyze_bytes(short)['observed'],app.analyze_bytes(long)['observed'])

    def test_irrelevant_controls_and_pressure_not_velocity(self):
        data=smf(event(1,60,64)+b'\x00\xb0\x07\x01\x00\xb0\x0b\x7f\x00\xa0\x3c\x60\x00\xd0\x60'+EOT)
        self.assertEqual(app.analyze_bytes(data)['observed']['mean'],64)

    def test_input_options_rejected(self):
        for kwargs in [{'channels':[]},{'channels':[0]},{'channels':[True]},{'channels':[1,1]},{'tracks':[1]},{'notes':[128]},{'notes':'60'}]:
            with self.subTest(kwargs=kwargs),self.assertRaises(app.VelocityReportError):
                app.analyze_bytes(fixture([64]),**kwargs)

    def test_port_metadata_rejected(self):
        with self.assertRaises(app.VelocityReportError):
            app.analyze_bytes(smf(b'\x00\xff\x21\x01\x01'+event(1,60,64)+EOT))

    def test_unsupported_formats_and_framing(self):
        data=fixture([64])
        bad=[b'',data[:10],data[:-1],data+b'junk',smf(EOT,kind=2),smf(EOT,division=0),smf(EOT,division=0xe728),smf(EOT,EOT,kind=0)]
        for value in bad:
            with self.subTest(length=len(value)),self.assertRaises(app.VelocityReportError): app.analyze_bytes(value)

    def test_invalid_event_and_missing_end(self):
        for t in [b'\x00\x90\x3c',b'\x00\x3c\x40'+EOT,EOT+event(1,60,64),event(1,60,64)]:
            with self.subTest(t=t),self.assertRaises(app.VelocityReportError): app.analyze_bytes(smf(t))

    def test_explicit_limits(self):
        for name,value,data in [('MAX_BYTES',10,fixture([64])),('MAX_TRACK_BYTES',2,fixture([64])),('MAX_TRACK_MESSAGES',1,fixture([64])),('MAX_MESSAGES',1,fixture([64])),('MAX_NOTE_EVENTS',1,fixture([64,65]))]:
            with self.subTest(name=name),patch.object(app,name,value),self.assertRaises(app.VelocityReportError):app.analyze_bytes(data)

    def test_cli_success_failure_and_no_file_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'synthetic.mid'; raw=fixture([24,48]);p.write_bytes(raw)
            out,err=io.StringIO(),io.StringIO()
            with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):rc=app.main([str(p),'--channels','1'])
            self.assertEqual(rc,0);self.assertEqual(json.loads(out.getvalue())['observed']['mean'],36);self.assertEqual(err.getvalue(),'')
            self.assertEqual(p.read_bytes(),raw);self.assertEqual([x.name for x in Path(tmp).iterdir()],['synthetic.mid'])
            out,err=io.StringIO(),io.StringIO()
            with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):rc=app.main([str(p),'--notes','200'])
            self.assertEqual(rc,2);self.assertEqual(out.getvalue(),'');self.assertIn('Fehler:',err.getvalue())
            with self.assertRaises(app.VelocityReportError):app.analyze_file(tmp)

    def test_report_json_and_demo_exact(self):
        p=Path(__file__).parent
        actual=app.analyze_file(p/'demo.mid',channels=[1])
        self.assertEqual(actual,json.loads((p/'example-report.json').read_text()))
        self.assertNotIn('filename',actual['input']);self.assertNotIn('path',actual['input'])
        json.dumps(actual,allow_nan=False)


if __name__=='__main__': unittest.main()
