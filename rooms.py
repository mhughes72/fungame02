ROOMS = {
    "room_1": {
        "name": "Study",
        "description": "A dusty study with a fireplace and a portrait.",
        "exits": {"north": "room_2"},
        "monsters": [
            {
                "name": "ghost",
                "health": 30,
                "max_health": 30,
                "defense": 3,
                "damage": 8,
                "weaknesses": ["magic"],
                "drops": {"gold": 10, "item": None}
            }
        ],
        "items": [
            {"name": "rusty key",        "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0},
            {"name": "old book",         "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0},
            {"name": "magnifying glass", "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0},
            {"name": "quill pen",        "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0},
            {"name": "secret letter",    "hidden": True,  "revealed_by": "old book", "openable": False, "is_open": False, "gold": 0},
            {"name": "wooden chest",     "hidden": False, "revealed_by": None, "openable": True,  "is_open": False, "gold": 15},
            {"name": "letter opener", "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0, "damage": 3, "weapon_type": "blade"},

        ],
        "npcs": [
            {
                "name": "Professor Aldric",
                "description": "A frail old man hunched over a desk, scribbling notes by candlelight.",
                "personality": "Speaks in riddles, knows secrets about the mansion, cryptic but helpful if you earn his trust.",
                "knowledge": "Knows about the ghost, the secret letter behind the old book, and that the rusty key opens something in the basement.",
                "can_search_web": False,
            },
            {
                "name": "The Oracle",
                "description": "A mysterious figure hunched over a glowing crystal ball, muttering about distant lands.",
                "personality": "Speaks with authority on matters of the outside world. Dry, precise, and occasionally sardonic.",
                "knowledge": "Has an uncanny knowledge of American politics, current events, and world affairs.",
                "can_search_web": True,
            },
        ],
    },
    "room_2": {
        "name": "Hallway",
        "description": "A narrow hallway lined with cracked mirrors.",
        "exits": {"south": "room_1", "east": "room_3", "west": "room_4", "north": "room_5"},
        "monsters": [],
        "items": [],
        "npcs": [
            {
                "name": "Lady Vespera",
                "description": "A strikingly beautiful woman draped in deep crimson, her smile never quite reaching her dark, unblinking eyes.",
                "personality": "Charming, seductive, and unnervingly calm. Speaks in a way that makes everything sound like a veiled threat. Never raises her voice. Always seems to know more than she lets on.",
                "knowledge": "Knows the true nature of every monster in the mansion, hints that not everything is what it seems, and seems suspiciously familiar with the layout of the basement.",
                "can_search_web": False,
                "can_send_email": False,
            },
            {
                "name": "Shadow",
                "description": "A sleek black cat with luminous amber eyes who sits with the posture of someone who has seen civilizations rise and fall.",
                "personality": "Speaks slowly and cryptically as if imparting ancient wisdom, but everything it says is either completely obvious, total nonsense, or accidentally wrong. Enormously self-confident about all of it.",
                "knowledge": "Thinks it knows everything but is usually wrong. Occasionally stumbles onto something true completely by accident.",
                "can_search_web": False,
                "can_send_email": False,
            },
            {
                "name": "Aldous the Peddler",
                "description": "A stout man buried under layers of coats and belts, every inch of him bristling with weapons, pouches, and things that clink.",
                "personality": "Enthusiastic, fast-talking, and absolutely relentless. Genuinely believes every item he sells is the greatest thing you will ever own. Takes rejection personally but recovers instantly. Cannot go more than two sentences without mentioning a deal.",
                "knowledge": "Knows the value of every weapon and piece of armour in the mansion. Has strong opinions about combat tactics and will share them whether asked or not.",
                "can_search_web": False,
                "can_send_email": False,
            },
    ],
    },
    "room_3": {
        "name": "Kitchen",
        "description": "A decaying kitchen with rusted utensils and a faint smell of rot.",
        "exits": {"west": "room_2", "north": "room_6"},
        "monsters": [
            {
                "name": "giant rat",
                "health": 20,
                "max_health": 20,
                "defense": 1,
                "damage": 5,
                "weaknesses": ["blade"],
                "drops": {"gold": 5, "item": None}
            }
        ],
        "items": [
            {"name": "knife",               "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0},
            {"name": "loaf of stale bread", "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0},
            {"name": "battered tin box",    "hidden": False, "revealed_by": None, "openable": True,  "is_open": False, "gold": 8},
            {"name": "kitchen knife", "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0, "damage": 5, "weapon_type": "blade"},


        ],
        "npcs": [],
    },
    "room_4": {
        "name": "Library",
        "description": "Tall shelves filled with ancient, crumbling books. A ladder rests against one shelf.",
        "exits": {"east": "room_2", "north": "room_7"},
        "monsters": [
            {
                "name": "shadow",
                "health": 25,
                "max_health": 25,
                "defense": 4,
                "damage": 10,
                "weaknesses": ["magic", "fire"],
                "drops": {"gold": 8, "item": None}
            }
        ],
        "items": [
            {"name": "ancient tome", "hidden": False, "revealed_by": None},
            {"name": "candle",       "hidden": False, "revealed_by": None},
            {"name": "hidden note",  "hidden": True,  "revealed_by": "ancient tome"},
            {"name": "fire poker", "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0, "damage": 6, "weapon_type": "blunt"},

        ],
        "npcs": [],
    },
    "room_5": {
        "name": "Grand Foyer",
        "description": "A vast foyer with a chandelier swaying slightly above. The front door is sealed shut.",
        "exits": {"south": "room_2", "north": "room_8"},
        "monsters": [],
        "items": [
            {"name": "silver coin",         "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0},
            {"name": "ornate box",          "hidden": False, "revealed_by": None, "openable": True,  "is_open": False, "gold": 30},

        ],
        "npcs": [],
    },
    "room_6": {
        "name": "Pantry",
        "description": "A cramped pantry with empty shelves and scattered jars.",
        "exits": {"south": "room_3"},
        "monsters": [
            {
                "name": "spider swarm",
                "health": 15,
                "max_health": 15,
                "defense": 0,
                "damage": 6,
                "weaknesses": ["fire", "blade"],
                "drops": {"gold": 3, "item": "glass jar"}
            }
        ],
        "items": [
            {"name": "glass jar",   "hidden": False, "revealed_by": None},
            {"name": "poison vial", "hidden": True,  "revealed_by": "glass jar"},
        ],
        "npcs": [],
    },
    "room_7": {
        "name": "Secret Room",
        "description": "A hidden chamber behind the bookshelf, filled with strange symbols.",
        "exits": {"south": "room_4"},
        "monsters": [
            {
                "name": "wraith",
                "health": 40,
                "max_health": 40,
                "defense": 6,
                "damage": 14,
                "weaknesses": ["magic"],
                "drops": {"gold": 20, "item": None}
            }
        ],
        "items": [
            {"name": "strange amulet", "hidden": False, "revealed_by": None},
            {"name": "ritual dagger",  "hidden": True,  "revealed_by": "strange amulet"},
            {"name": "ritual dagger", "hidden": True, "revealed_by": "strange amulet", "openable": False, "is_open": False, "gold": 0, "damage": 12, "weapon_type": "blade"},
            {"name": "magic staff",   "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0, "damage": 15, "weapon_type": "magic"},

        ],
        "npcs": [],
    },
    "room_8": {
        "name": "Dining Hall",
        "description": "A long dining table covered in dust, set as if awaiting guests who never came.",
        "exits": {"south": "room_5", "east": "room_9", "west": "room_10"},
        "monsters": [
            {
                "name": "vampire",
                "health": 60,
                "max_health": 60,
                "defense": 8,
                "damage": 18,
                "weaknesses": ["blade", "fire"],
                "drops": {"gold": 40, "item": "golden goblet"}
            }
        ],
        "items": [
            {"name": "golden goblet",       "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0},
            {"name": "invitation",          "hidden": True,  "revealed_by": "golden goblet", "openable": False, "is_open": False, "gold": 0},
            {"name": "dusty lockbox",       "hidden": False, "revealed_by": None, "openable": True,  "is_open": False, "gold": 50},

        ],
        "npcs": [],
    },
    "room_9": {
        "name": "Garden",
        "description": "An overgrown garden under a dark sky. The air is eerily still.",
        "exits": {"west": "room_8"},
        "monsters": [
            {
                "name": "werewolf",
                "health": 50,
                "max_health": 50,
                "defense": 6,
                "damage": 15,
                "weaknesses": ["silver"],
                "drops": {"gold": 30, "item": None}
            }
        ],
        "items": [
            {"name": "herbs",         "hidden": False, "revealed_by": None},
            {"name": "silver bullet", "hidden": True,  "revealed_by": "herbs"},
            {"name": "silver bullet", "hidden": True, "revealed_by": "herbs", "openable": False, "is_open": False, "gold": 0, "damage": 20, "weapon_type": "silver"},

        ],
        "npcs": [],
    },
    "room_10": {
        "name": "Basement Stairs",
        "description": "A creaky staircase leading down into darkness.",
        "exits": {"east": "room_8", "down": "room_11"},
        "monsters": [],
        "items": [],
        "npcs": [],
    },
    "room_11": {
        "name": "Basement",
        "description": "A damp basement with chains on the walls and a cold draft.",
        "exits": {"up": "room_10"},
        "monsters": [
            {
                "name": "ghoul",
                "health": 35,
                "max_health": 35,
                "defense": 4,
                "damage": 12,
                "weaknesses": ["blade", "magic"],
                "drops": {"gold": 15, "item": None}
            }
        ],
        "items": [
            {"name": "iron key",            "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0},
            {"name": "broken chain",        "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0},
            {"name": "locket",              "hidden": True,  "revealed_by": "broken chain", "openable": False, "is_open": False, "gold": 0},
            {"name": "rotting satchel",     "hidden": False, "revealed_by": None, "openable": True,  "is_open": False, "gold": 12},
            {"name": "rusty sword", "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0, "damage": 8, "weapon_type": "blade"},

                    ],
        "npcs": [],
    },
}