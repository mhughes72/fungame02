"""
Generate DALL-E 3 portrait images for all NPCs and monsters in rooms.json.
Saves to static/npcs/<slug>.png and static/monsters/<slug>.png.

Usage:
    python scripts/generate_encounter_images.py
    python scripts/generate_encounter_images.py --npc "Professor Aldric"
    python scripts/generate_encounter_images.py --monster "giant rat"
    python scripts/generate_encounter_images.py --overwrite
    python scripts/generate_encounter_images.py --dry-run

Cost estimate: ~$0.08 per image (DALL-E 3 standard, 1024x1792).
With the default mansion contents (~12 unique characters), expect ~$0.96 total.
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
    "Gothic dark fantasy character portrait, oil painting style, moody candlelit atmosphere, "
    "deep shadows, Victorian mansion setting, dramatic chiaroscuro lighting, "
    "muted earth tones with amber highlights, highly detailed, no text. "
    "Portrait orientation (vertical), head and shoulders to mid-torso framing."
)

NPC_PROMPTS = {
    "professor_aldric": (
        "An elderly scholar in a Victorian mansion study. Gaunt face with piercing grey eyes, wild white "
        "hair, long dark academic robes adorned with worn leather patches. Surrounded by towering stacks "
        "of arcane books and crumbling manuscripts. An air of cryptic wisdom and barely-concealed secrets."
    ),
    "the_oracle": (
        "A mysterious robed figure seated on a stone throne. Face partially obscured by a deep hood, "
        "only piercing luminous eyes visible. Hands folded with unnatural stillness. An aura of vast, "
        "dry authority and otherworldly knowledge. No warmth, only precision."
    ),
    "aldous_the_peddler": (
        "A rotund, fast-talking Victorian merchant with a waxed moustache and an enormous coat covered "
        "in hidden pockets bulging with wares. Gleaming eyes full of enthusiasm, one hand gesturing "
        "grandly, the other clutching a worn leather satchel. Absolutely relentless energy."
    ),
    "lady_vespera": (
        "An elegant pale noblewoman in a sweeping Victorian black gown with dark lace. Luminous skin, "
        "dark penetrating eyes with an unsettling depth, a faint knowing smile. Perfectly composed, impossibly still. "
        "An aura of dangerous allure and calm menace. Long dark hair pinned with a silver comb."
    ),
    "shadow": (
        "A tall humanoid figure composed entirely of living shadow, edges blurring and shifting. "
        "Two faint points of pale light where eyes would be. Ancient, patient, unhurried. "
        "A sense of vast age and cryptic wisdom radiating from the darkness itself."
    ),
    "mara_the_herbalist": (
        "A lean, weathered woman crouching in an overgrown Victorian garden at night. Dark practical "
        "clothing, hands stained with soil and plant matter, bundles of dried herbs hanging from her "
        "belt. Sharp, knowing eyes that have seen too much. Calm and unhurried, utterly at ease among "
        "the dead garden beds. An air of quiet authority and contained danger."
    ),
}

MONSTER_PROMPTS = {
    "ghost": (
        "A translucent spectral figure drifting through a Victorian hallway. Pale blue-white glow, "
        "features half-visible and mournful, tattered clothing from a past era, trailing wisps of "
        "ethereal mist. An expression of sorrow and unfinished business."
    ),
    "giant_rat": (
        "An oversized mangy rat the size of a large dog, fur matted and filthy. Red glinting eyes, "
        "yellowed broken teeth bared in a snarl, claws scraping stone floor. Diseased and aggressive, "
        "coiled to spring from the shadows of a Victorian cellar."
    ),
    "shadow": (
        "A predatory mass of living darkness in a stone corridor. Clawed limbs dissolving and reforming "
        "at the edges, two cold white eyes burning within the void. Radiates menace and hunger. "
        "This shadow does not give wisdom — it hunts."
    ),
    "spider_swarm": (
        "A heaving mass of hundreds of black spiders flowing across a stone floor like a living tide. "
        "Individual spiders visible at the edges — glossy bodies, too many eyes. "
        "Moonlight glinting off silk threads as they surge forward together."
    ),
    "wraith": (
        "A skeletal undead figure wrapped in tattered black robes, barely physical. "
        "Long bony fingers reaching outward, hollow eye sockets glowing with cold pale fire. "
        "Hovering inches off the ground, surrounded by a chill aura of death."
    ),
    "vampire": (
        "A pale aristocratic vampire in a Victorian evening coat, immaculate despite everything. "
        "Chalk-white skin, dark predatory eyes, a faint contemptuous smile revealing elongated canines. "
        "Impossibly still, radiating ancient power and effortless menace."
    ),
    "werewolf": (
        "A massive half-man half-wolf creature mid-transformation. Torn Victorian clothing stretched "
        "over a hunched muscular frame, claws extended, maw open in a silent snarl. "
        "Amber eyes blazing with animal fury, coarse dark fur, impossible size."
    ),
    "ghoul": (
        "A gaunt undead figure with grey decaying skin stretched over protruding bones. "
        "Sunken milky eyes, mouth stretched too wide in a hollow grin, tattered funeral clothes. "
        "Crouched in a predatory stance, fingers ending in blackened talons."
    ),
}


def slugify(name: str) -> str:
    return name.lower().replace(' ', '_').replace("'", '').replace('-', '_')


def discover_from_rooms(rooms_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """
    Read rooms.json and return (npc_slug→name, monster_slug→name) dicts
    for anything not already in the hand-written prompt tables.
    Unknown entries are reported so prompts can be added.
    """
    data_dir = rooms_path.parent
    with open(rooms_path) as f:
        rooms = json.load(f)
    with open(data_dir / "npcs.json") as f:
        npcs = json.load(f)
    with open(data_dir / "monsters.json") as f:
        monsters = json.load(f)

    found_npcs: dict[str, str] = {}
    found_monsters: dict[str, str] = {}

    for room in rooms.values():
        for npc_id in room.get("npcs", []):
            name = npcs.get(npc_id, {}).get("name", npc_id)
            found_npcs[slugify(name)] = name
        for monster_id in room.get("monsters", []):
            name = monsters.get(monster_id, {}).get("name", monster_id)
            found_monsters[slugify(name)] = name

    return found_npcs, found_monsters


def generate_image(client: OpenAI, label: str, prompt: str, out_path: Path, dry_run: bool) -> None:
    full_prompt = f"{STYLE_PREFIX}. Subject: {prompt}"
    print(f"  Generating {label}...")
    if dry_run:
        print(f"  [DRY RUN] Would generate: {out_path}")
        print(f"  Prompt: {full_prompt[:120]}...")
        return

    response = client.images.generate(
        model="dall-e-3",
        prompt=full_prompt,
        size="1024x1792",  # portrait orientation (portrait_ratio)
        quality="standard",
        n=1,
        style="natural",  # prefer natural proportions
    )

    image_url = response.data[0].url
    img_data = requests.get(image_url, timeout=30).content
    out_path.write_bytes(img_data)
    print(f"  Saved to {out_path}")


def run_batch(
    client: OpenAI,
    targets: list[tuple[str, str, str, Path]],  # (slug, label, prompt, out_path)
    checksums: dict[str, str],
    overwrite: bool,
    dry_run: bool,
) -> None:
    to_generate = []
    for slug, label, prompt, out_path in targets:
        if should_generate(slug, prompt, checksums, overwrite):
            to_generate.append((slug, label, prompt, out_path))
        else:
            print(f"  Skipping {label} (unchanged)")

    if not to_generate:
        return

    for i, (slug, label, prompt, out_path) in enumerate(to_generate):
        generate_image(client, label, prompt, out_path, dry_run)
        if not dry_run:
            checksums[slug] = hash_prompt(prompt)
        if i < len(to_generate) - 1 and not dry_run:
            time.sleep(13)  # stay under DALL-E rate limit (5/min on tier 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate NPC and monster portraits via DALL-E 3")
    parser.add_argument("--npc",      help="Generate a single NPC by name (e.g. 'Professor Aldric')")
    parser.add_argument("--monster",  help="Generate a single monster by name (e.g. 'giant rat')")
    parser.add_argument("--overwrite", action="store_true", help="Regenerate all images (ignores checksums)")
    parser.add_argument("--dry-run",   action="store_true", help="Print what would be generated without calling the API")
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key and not args.dry_run:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = OpenAI(api_key=api_key or "dry-run")

    rooms_path = Path("data/rooms.json")
    npc_dir     = Path("static/npcs")
    monster_dir = Path("static/monsters")
    npc_dir.mkdir(parents=True, exist_ok=True)
    monster_dir.mkdir(parents=True, exist_ok=True)

    cache_path = Path("scripts/.encounter_checksums.json")
    checksums = load_checksums(cache_path)

    # Warn about any NPCs/monsters in rooms.json that have no hand-written prompt
    found_npcs, found_monsters = discover_from_rooms(rooms_path)
    for slug, name in found_npcs.items():
        if slug not in NPC_PROMPTS:
            print(f"  WARNING: NPC '{name}' (slug: {slug}) has no prompt — add one to NPC_PROMPTS")
    for slug, name in found_monsters.items():
        if slug not in MONSTER_PROMPTS:
            print(f"  WARNING: Monster '{name}' (slug: {slug}) has no prompt — add one to MONSTER_PROMPTS")

    # Build target list: (slug, label, prompt, out_path)
    if args.npc:
        slug = slugify(args.npc)
        if slug not in NPC_PROMPTS:
            print(f"ERROR: No prompt defined for NPC '{args.npc}' (slug: {slug})")
            sys.exit(1)
        npc_targets = [(slug, args.npc, NPC_PROMPTS[slug], npc_dir / f"{slug}.png")]
        monster_targets = []

    elif args.monster:
        slug = slugify(args.monster)
        if slug not in MONSTER_PROMPTS:
            print(f"ERROR: No prompt defined for monster '{args.monster}' (slug: {slug})")
            sys.exit(1)
        npc_targets = []
        monster_targets = [(slug, args.monster, MONSTER_PROMPTS[slug], monster_dir / f"{slug}.png")]

    else:
        npc_targets = [
            (slug, name, NPC_PROMPTS[slug], npc_dir / f"{slug}.png")
            for slug, name in sorted(found_npcs.items())
            if slug in NPC_PROMPTS
        ]
        monster_targets = [
            (slug, name, MONSTER_PROMPTS[slug], monster_dir / f"{slug}.png")
            for slug, name in sorted(found_monsters.items())
            if slug in MONSTER_PROMPTS
        ]

    all_targets = npc_targets + monster_targets
    if not all_targets:
        print("Nothing to generate.")
        return

    print(f"\nNPCs:     {len(npc_targets)}")
    print(f"Monsters: {len(monster_targets)}")

    if npc_targets:
        print("\n--- NPCs ---")
        run_batch(client, npc_targets, checksums, args.overwrite, args.dry_run)

    if monster_targets:
        print("\n--- Monsters ---")
        run_batch(client, monster_targets, checksums, args.overwrite, args.dry_run)

    if not args.dry_run:
        save_checksums(cache_path, checksums)

    print("\nDone.")


if __name__ == "__main__":
    main()
