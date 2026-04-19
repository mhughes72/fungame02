"""
Generate DALL-E 3 images for each room in rooms.json.
Saves to static/rooms/<room_id>.png.

Usage:
    python scripts/generate_room_images.py
    python scripts/generate_room_images.py --room room_3   # single room
    python scripts/generate_room_images.py --overwrite      # regenerate all
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from openai import OpenAI

from image_cache import load_checksums, save_checksums, hash_prompt, should_generate

load_dotenv()

STYLE_PREFIX = (
    "Gothic dark fantasy illustration, oil painting style, moody candlelit atmosphere, "
    "deep shadows, haunted Victorian mansion interior, dramatic chiaroscuro lighting, "
    "muted earth tones with amber highlights, highly detailed, no text, no people"
)

ROOM_PROMPTS = {
    "room_1":  "A dusty Victorian study: stone fireplace crackling, a large oil portrait looming on the wall, "
               "overflowing desk with quill and inkpot, leather-bound books stacked in corners, a wooden chest beneath the desk.",
    "room_2":  "A narrow mansion hallway: both walls lined floor-to-ceiling with ornate cracked mirrors reflecting infinite dark corridors, "
               "flickering wall sconces, warped floorboards, a sense of endless recursive depth.",
    "room_3":  "A decaying Victorian kitchen: rusted iron pots hanging from hooks, a stone sink with dripping tap, "
               "broken crockery strewn on a worktable, dark stains on the flagstone floor, a single guttering candle.",
    "room_4":  "A grand mansion library: towering shelves of crumbling leather-bound tomes reaching the vaulted ceiling, "
               "a wooden ladder leaning against one shelf, a single candle on a reading stand, ancient dust motes in the air.",
    "room_5":  "A vast Victorian foyer: enormous crystal chandelier swaying above, marble floor, sweeping staircase, "
               "the enormous front door sealed with heavy iron chains, moonlight filtering through high windows.",
    "room_6":  "A cramped stone pantry: bare wooden shelves with only a few cracked jars, cobwebs in every corner, "
               "low ceiling, a small barred window letting in a sliver of pale light, spider webs glistening.",
    "room_7":  "A hidden secret chamber behind a bookshelf: stone walls carved with arcane symbols and sigils, "
               "a stone altar at the centre, ritual candles, an aura of forbidden knowledge, no windows.",
    "room_8":  "A vast Victorian dining hall: long table set with dusty silver candelabras and cobwebbed place settings, "
               "high-backed chairs, tattered tapestries, a cold fireplace, an atmosphere of an interrupted feast frozen in time.",
    "room_9":  "An overgrown Victorian garden at night: iron gates, dead twisted trees, moonlit fog rolling over cracked stone paths, "
               "strangling vines on crumbling walls, eerie stillness, no wind.",
    "room_10": "A creaking wooden staircase descending into pitch blackness: bare stone walls glistening with moisture, "
               "a single torch guttering at the top, wrought-iron railing disappearing into the dark below.",
    "room_11": "A damp stone basement: heavy chains bolted to the walls, puddles on the floor reflecting a distant torch, "
               "arched stone ceiling, a cold draft from an unknown source, oppressive darkness at the edges.",
}


def generate_image(client: OpenAI, room_id: str, out_path: Path) -> None:
    prompt = f"{STYLE_PREFIX}. Scene: {ROOM_PROMPTS[room_id]}"
    print(f"  Generating {room_id}...")

    response = client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        size="1792x1024",
        quality="standard",
        n=1,
    )

    image_url = response.data[0].url
    img_data = requests.get(image_url, timeout=30).content
    out_path.write_bytes(img_data)
    print(f"  Saved to {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate room images via DALL-E 3")
    parser.add_argument("--room", help="Generate a single room (e.g. room_3)")
    parser.add_argument("--overwrite", action="store_true", help="Regenerate all images (ignores checksums)")
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = OpenAI(api_key=api_key)
    out_dir = Path("static/rooms")
    out_dir.mkdir(parents=True, exist_ok=True)

    cache_path = Path("scripts/.room_checksums.json")
    checksums = load_checksums(cache_path)

    rooms = [args.room] if args.room else sorted(ROOM_PROMPTS.keys())
    to_generate = []

    for room_id in rooms:
        if room_id not in ROOM_PROMPTS:
            print(f"Unknown room: {room_id}")
            continue

        prompt = f"{STYLE_PREFIX}. Scene: {ROOM_PROMPTS[room_id]}"
        if should_generate(room_id, prompt, checksums, args.overwrite):
            to_generate.append((room_id, prompt))
        else:
            print(f"  Skipping {room_id} (unchanged)")

    if not to_generate:
        print("All rooms up to date.")
        return

    print(f"Generating {len(to_generate)} room(s)...\n")

    for i, (room_id, prompt) in enumerate(to_generate):
        out_path = out_dir / f"{room_id}.png"
        generate_image(client, room_id, out_path)
        checksums[room_id] = hash_prompt(prompt)

        # Stay under DALL-E rate limits (5 images/min on tier 1)
        if i < len(to_generate) - 1:
            time.sleep(13)

    save_checksums(cache_path, checksums)
    print("Done.")


if __name__ == "__main__":
    main()
