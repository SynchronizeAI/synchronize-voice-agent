"""Offline proof of the voice comparison: every request goes to a loopback fake."""

import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

import voice_ab


class FakeElevenLabs(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.key = uuid4().hex
        self.calls = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        fake = self.server
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        fake.calls.append((self.path, body, self.headers.get("xi-api-key")))
        if self.headers.get("xi-api-key") != fake.key:
            self.send_response(401)
            self.end_headers()
            return
        # The audio names the model that made it, so the test can check the key.
        audio = body["model_id"].encode()
        self.send_response(200)
        self.send_header("Content-Type", "audio/basic")
        self.send_header("Content-Length", str(len(audio)))
        self.end_headers()
        self.wfile.write(audio)


class VoiceComparisonTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeElevenLabs()
        thread = threading.Thread(target=self.fake.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (self.fake.shutdown(), self.fake.server_close(), thread.join()))
        temp = tempfile.TemporaryDirectory(prefix="voice-ab-")
        self.addCleanup(temp.cleanup)
        self.out = Path(temp.name) / "out"
        env = patch.dict(os.environ, {"ELEVENLABS_API_KEY": self.fake.key, "ELEVENLABS_BASE_URL": self.fake.origin}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def cli(self, *args, expected=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = voice_ab.main(list(args))
        output = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(self.fake.key, output)
        self.assertEqual(result, expected, output)
        return output

    def test_make_writes_blind_pairs_with_dograh_call_settings(self):
        self.cli("make", "--voice", "Sora K - voice123", "--out", str(self.out))
        key = json.loads((self.out / "key.json").read_text())
        self.assertEqual(key["voice"], "voice123")
        self.assertEqual(len(key["sentences"]), len(voice_ab.SENTENCES))
        for row in key["sentences"]:
            self.assertEqual({row["A"], row["B"]}, set(voice_ab.MODELS))
            for label in ("A", "B"):
                wav = (self.out / f"s{row['n']:02d}-{label}.wav").read_bytes()
                self.assertEqual(wav[:4], b"RIFF")
                self.assertEqual(wav[8:12], b"WAVE")
                # mu-law (tag 7), mono, 8 kHz: what the phone carries.
                self.assertEqual(struct.unpack("<HHI", wav[20:28]), (7, 1, 8000))
                self.assertIn(row[label].encode(), wav)
            self.assertEqual(set(row["first_byte_seconds"]), set(voice_ab.MODELS))
        self.assertNotIn(self.fake.key, "".join(p.read_text(errors="ignore") for p in self.out.iterdir()))
        sheet = (self.out / "sheet.tsv").read_text()
        for model in voice_ab.MODELS:
            self.assertNotIn(model, sheet)
        dialogue = [c for c in self.fake.calls if c[1]["model_id"] == "eleven_v4_turbo"]
        flash = [c for c in self.fake.calls if c[1]["model_id"] == "eleven_flash_v2_5"]
        self.assertEqual(len(dialogue), len(voice_ab.SENTENCES))
        self.assertEqual(len(flash), len(voice_ab.SENTENCES))
        self.assertTrue(all(c[0] == "/v1/text-to-dialogue/stream?output_format=ulaw_8000" for c in dialogue))
        self.assertEqual(dialogue[0][1]["inputs"], [{"text": voice_ab.SENTENCES[0], "voice_id": "voice123"}])
        self.assertEqual(dialogue[0][1]["settings"], {"stability": 0.8})
        self.assertTrue(all(c[0] == "/v1/text-to-speech/voice123/stream?output_format=ulaw_8000" for c in flash))
        self.assertEqual(flash[0][1]["voice_settings"], {"stability": 0.8, "similarity_boost": 0.75})
        # A second make never overwrites the first pairs.
        output = self.cli("make", "--voice", "voice123", "--out", str(self.out), expected=1)
        self.assertIn("new or empty directory", output)

    def test_score_counts_preferences_by_model_and_needs_every_answer(self):
        self.cli("make", "--voice", "voice123", "--out", str(self.out))
        key = json.loads((self.out / "key.json").read_text())
        lines = (self.out / "sheet.tsv").read_text().splitlines()
        # Listener one prefers Flash everywhere; listener two answers "same" once.
        one = [lines[0]] + [
            f"{row['n']}\tx\t{'A' if row['A'] == 'eleven_flash_v2_5' else 'B'}\t" for row in key["sentences"]
        ]
        two = [lines[0]] + [f"{row['n']}\tx\t{'same' if row['n'] == 1 else 'a'}\t" for row in key["sentences"]]
        (self.out / "one.tsv").write_text("\n".join(one) + "\n")
        (self.out / "two.tsv").write_text("\n".join(two) + "\n")
        output = self.cli("score", "--out", str(self.out), str(self.out / "one.tsv"), str(self.out / "two.tsv"))
        result = json.loads(output)
        count = len(voice_ab.SENTENCES)
        self.assertEqual(result["listeners"]["one"], {"eleven_v4_turbo": 0, "eleven_flash_v2_5": count, "same": 0})
        a_models = [row["A"] for row in key["sentences"][1:]]
        self.assertEqual(
            result["listeners"]["two"],
            {
                "eleven_v4_turbo": a_models.count("eleven_v4_turbo"),
                "eleven_flash_v2_5": a_models.count("eleven_flash_v2_5"),
                "same": 1,
            },
        )
        self.assertEqual(set(result["median_first_byte_seconds"]), set(voice_ab.MODELS))
        (self.out / "short.tsv").write_text("\n".join(one[:3]) + "\n")
        output = self.cli("score", "--out", str(self.out), str(self.out / "short.tsv"), expected=1)
        self.assertIn("answer every sentence", output)
        (self.out / "bad.tsv").write_text(lines[0] + "\n1\tx\tmaybe\t\n")
        output = self.cli("score", "--out", str(self.out), str(self.out / "bad.tsv"), expected=1)
        self.assertIn("write A, B or same", output)

    def test_a_refused_key_stops_without_printing_it(self):
        os.environ["ELEVENLABS_API_KEY"] = "wrong" + uuid4().hex
        output = self.cli("make", "--voice", "voice123", "--out", str(self.out), expected=1)
        self.assertIn("HTTP 401", output)
        self.assertNotIn(os.environ["ELEVENLABS_API_KEY"], output)


if __name__ == "__main__":
    unittest.main()
