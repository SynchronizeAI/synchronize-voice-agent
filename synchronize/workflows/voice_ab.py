"""Blind listening comparison of two ElevenLabs models on one voice, at phone quality.

make (the owner's ElevenLabs key, ELEVENLABS_API_KEY in the environment): synthesizes the Korean
line's own sentences with both models on the same voice, with the settings Dograh 1.51 uses on a call
(stability 0.8, and similarity 0.75 for the non-dialogue models; api/services/pipecat/service_factory.py:976-991),
as 8 kHz mu-law, the audio Twilio carries. A v3/v4 model goes through Text to Dialogue as on a call
(Dograh's pattern ^eleven_v[34], api/services/configuration/registry.py:1385), any other through Text to Speech.
For each sentence one model is A and the other B, drawn at random; the files are sNN-A.wav and sNN-B.wav,
sheet.tsv is the form each listener fills, and key.json (never shown to listeners) says which is which and
how long each model took to its first audio byte from this machine.
score: reads the filled sheets and the key and prints each listener's preferences by model.
No key is printed or stored.
"""

import argparse
import json
import os
from pathlib import Path
import random
import re
import statistics
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import golive

MODELS = ("eleven_v4_turbo", "eleven_flash_v2_5")
DIALOGUE = re.compile(r"^eleven_v[34]")
FORMAT = "ulaw_8000"
SENTENCES = (
    "안녕하세요, 저희 회사 AI 비서입니다. 통화 내용은 녹음되어 담당자에게 전달됩니다. 어떤 일로 전화하셨나요?",
    "네, 성함이 어떻게 되세요?",
    "김민지님 맞으세요?",
    "실례지만 다시 한번 말씀해 주시겠어요?",
    "확인해 보겠습니다.",
    "기록에는 11월 10일 월요일에 방문이 잡혀 있습니다.",
    "죄송하지만 지금은 전화를 받을 수 있는 사람이 없습니다. 연락받기 편한 시간이 언제세요?",
    "전화 주셔서 감사합니다. 담당자가 확인해서 전화 주신 번호로 연락드리겠습니다.",
)
CHOICES = ("A", "B", "same")


def voice_id(value):
    """Dograh stores either the id or "Name - id" (service_factory.py:966-970)."""
    return value.split(" - ")[1] if " - " in value else value


def mulaw_wav(audio):
    """A WAV file around raw 8 kHz mono mu-law (format tag 7), so any player opens it."""
    fmt = struct.pack("<HHIIHHH", 7, 1, 8000, 8000, 1, 8, 0)
    fact = struct.pack("<I", len(audio))
    pad = b"\0" if len(audio) % 2 else b""
    body = (
        b"WAVE"
        + b"fmt " + struct.pack("<I", len(fmt)) + fmt
        + b"fact" + struct.pack("<I", len(fact)) + fact
        + b"data" + struct.pack("<I", len(audio)) + audio + pad
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def synthesize(base, key, model, voice, text):
    """The audio and the seconds to its first byte."""
    query = urllib.parse.urlencode({"output_format": FORMAT})
    if DIALOGUE.match(model):
        url = f"{base}/v1/text-to-dialogue/stream?{query}"
        body = {"inputs": [{"text": text, "voice_id": voice}], "model_id": model, "settings": {"stability": 0.8}}
    else:
        url = f"{base}/v1/text-to-speech/{urllib.parse.quote(voice, safe='')}/stream?{query}"
        body = {"text": text, "model_id": model, "voice_settings": {"stability": 0.8, "similarity_boost": 0.75}}
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "xi-api-key": key, "Accept": "audio/basic"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), golive.NoRedirect())
    started = time.monotonic()
    try:
        with opener.open(req, timeout=60) as response:
            first = response.read(1)
            first_byte = time.monotonic() - started
            audio = first + response.read()
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        raise golive.GoLiveError(f"ElevenLabs {model} answered HTTP {status}") from None
    except OSError:
        raise golive.GoLiveError(f"ElevenLabs {model} request failed (details suppressed)") from None
    golive.require(bool(audio), f"ElevenLabs {model} returned no audio")
    return audio, round(first_byte, 3)


def make(args):
    key = golive.env_value("ELEVENLABS_API_KEY")
    base = golive.origin(os.environ.get("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io"), "ELEVENLABS_BASE_URL")
    voice = voice_id(args.voice)
    golive.require(len(set(args.models)) == 2, "--models takes two different models")
    golive.require(not args.out.exists() or not any(args.out.iterdir()), "--out must be a new or empty directory")
    args.out.mkdir(parents=True, exist_ok=True)
    draw = random.SystemRandom()
    rows = []
    for number, text in enumerate(SENTENCES, 1):
        a, b = args.models if draw.random() < 0.5 else tuple(reversed(args.models))
        first_byte = {}
        for label, model in (("A", a), ("B", b)):
            audio, first_byte[model] = synthesize(base, key, model, voice, text)
            (args.out / f"s{number:02d}-{label}.wav").write_bytes(mulaw_wav(audio))
        rows.append({"n": number, "text": text, "A": a, "B": b, "first_byte_seconds": first_byte})
        print(f"s{number:02d} done")
    (args.out / "key.json").write_text(
        json.dumps({"voice": voice, "models": list(args.models), "format": FORMAT, "sentences": rows}, ensure_ascii=False, indent=1) + "\n"
    )
    sheet = ["n\tsentence\tbetter (A, B or same)\tnote"] + [f"{row['n']}\t{row['text']}\t\t" for row in rows]
    (args.out / "sheet.tsv").write_text("\n".join(sheet) + "\n")
    print(f"{len(rows)} pairs in {args.out}. Give each listener the wav files and a copy of sheet.tsv, never key.json.")
    return 0


def score(args):
    key = json.loads((args.out / "key.json").read_text())
    by_number = {row["n"]: row for row in key["sentences"]}
    totals = {model: 0 for model in key["models"]}
    totals["same"] = 0
    listeners = {}
    for sheet in args.sheets:
        counts = {model: 0 for model in key["models"]}
        counts["same"] = 0
        answered = 0
        for line in sheet.read_text().splitlines()[1:]:
            cells = line.split("\t")
            if len(cells) < 3 or not cells[0].strip().isdigit():
                continue
            choice = cells[2].strip()
            choice = choice.upper() if choice.upper() in ("A", "B") else choice.lower()
            golive.require(choice in CHOICES, f"{sheet.name} line {cells[0]}: write A, B or same")
            row = by_number[int(cells[0])]
            counts[row[choice] if choice in ("A", "B") else "same"] += 1
            answered += 1
        golive.require(answered == len(by_number), f"{sheet.name}: answer every sentence ({answered} of {len(by_number)})")
        listeners[sheet.stem] = counts
        for name, count in counts.items():
            totals[name] += count
    first_byte = {
        model: statistics.median(row["first_byte_seconds"][model] for row in key["sentences"])
        for model in key["models"]
    }
    print(json.dumps({"listeners": listeners, "total": totals, "median_first_byte_seconds": first_byte}, ensure_ascii=False, indent=1))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("make", "score"))
    parser.add_argument("--voice", help="the Korean line's ElevenLabs voice id (or Dograh's 'Name - id')")
    parser.add_argument("--models", nargs=2, default=list(MODELS))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("sheets", nargs="*", type=Path, help="score: the filled sheets")
    # Intermixed: the sheets may follow --out (plain parse_args binds the empty list before it).
    args = parser.parse_intermixed_args(argv)
    try:
        if args.command == "make":
            golive.require(bool(args.voice), "--voice is required")
            return make(args)
        golive.require(bool(args.sheets), "score needs the filled sheets")
        return score(args)
    except golive.GoLiveError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
