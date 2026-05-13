#!/usr/bin/env python3
"""
simulate.py — Automated playtesting harness for the haunted mansion game.

Runs AI-controlled playthroughs with multiple strategies, collects mechanics
metrics, detects balance issues, and exports a JSON report for analysis.

Usage:
    python simulate.py
    python simulate.py --runs 30 --strategies balanced cautious aggressive
    python simulate.py --output report.json --runs 50

No server or LLM required — reproduces exact game mechanics directly.
"""

import argparse
import copy
import json
import os
import random
import sys
from collections import defaultdict
from datetime import datetime


# ── Constants (mirrored from utils.py) ────────────────────────────────────────

FLEE_SUCCESS_THRESHOLD = 40       # d100 must exceed this to flee
WEAKNESS_BONUS_DAMAGE  = 5
ARMOR_REDUCTION_RATE   = 0.05
ARMOR_REDUCTION_CAP    = 0.75
MAX_TURNS              = 400

RITUAL_ITEMS = {"strange amulet", "blood pact", "unmaking flame"}
RITUAL_ROOM  = "room_5"

ITEM_DEFAULTS = {
    "damage": 0, "weapon_type": None, "armor_slot": None,
    "armor_rating": 0, "heal_amount": 0, "openable": False,
    "gold": 0, "is_open": False,
}

# Items the bot actively wants to pick up (by name keyword)
WEAPON_TYPES   = {"blade", "magic", "blunt", "silver", "fire"}
PRIORITY_ITEMS = RITUAL_ITEMS | {"health potion", "iron key", "rusty key"}


# ── Data loading ───────────────────────────────────────────────────────────────

def load_game_data():
    data_dir = "data"
    with open(os.path.join(data_dir, "rooms.json"))    as f: rooms_raw    = json.load(f)
    with open(os.path.join(data_dir, "monsters.json")) as f: monster_cat  = json.load(f)
    with open(os.path.join(data_dir, "items.json"))    as f: item_cat     = json.load(f)
    with open(os.path.join(data_dir, "npcs.json"))     as f: npc_cat      = json.load(f)

    rooms = {}
    for room_id, raw in rooms_raw.items():
        room = copy.deepcopy(raw)
        room["monsters"] = [copy.deepcopy(monster_cat[m]) for m in room.get("monsters", [])]
        room["npcs"]     = [copy.deepcopy(npc_cat[n])     for n in room.get("npcs", [])]
        resolved = []
        for ref in room.get("items", []):
            entry  = copy.deepcopy(item_cat.get(ref["id"], {}))
            merged = {**ITEM_DEFAULTS, **entry, **ref}
            resolved.append(merged)
        room["items"] = resolved
        rooms[room_id] = room

    return rooms, item_cat, monster_cat, npc_cat


# ── Mechanics helpers ──────────────────────────────────────────────────────────

def total_armor(player):
    inventory     = player.get("inventory", [])
    equipped_slots = player.get("equipped_armor", {})
    total = 0
    for name in equipped_slots.values():
        item = next((i for i in inventory if i["name"] == name), None)
        if item:
            total += item.get("armor_rating", 0)
    return total

def get_weapon(player):
    name = player.get("equipped_weapon")
    if not name:
        return None
    return next((i for i in player.get("inventory", []) if i["name"] == name), None)

def best_weapon_in(items):
    """Return the highest-damage weapon in an item list, or None."""
    weapons = [i for i in items if i.get("weapon_type") and i.get("damage", 0) > 0]
    return max(weapons, key=lambda i: i["damage"], default=None)

def best_armor_in(items, slot):
    """Return highest-rating armor in slot from an item list, or None."""
    pieces = [i for i in items if i.get("armor_slot") == slot]
    return max(pieces, key=lambda i: i.get("armor_rating", 0), default=None)

def best_weapon_for(player, monster) -> dict | None:
    """Pick the inventory weapon maximising effective DPR against this monster."""
    weaknesses = set(monster.get("weaknesses", []))
    defense    = monster["defense"]
    best, best_dpr = None, -1.0
    for item in player.get("inventory", []):
        if not item.get("weapon_type") or not item.get("damage", 0):
            continue
        bonus = WEAKNESS_BONUS_DAMAGE if item["weapon_type"] in weaknesses else 0
        dpr   = max(0.0, item["damage"] + 3.5 + bonus - defense)
        if dpr > best_dpr:
            best, best_dpr = item, dpr
    return best


def win_probability_estimate(player, monster):
    """
    Analytic estimate of P(player wins), accounting for available healing.
    Uses the best available weapon against this specific monster.
    Returns (prob, est_rounds, est_dmg_taken).
    """
    weapon    = best_weapon_for(player, monster) or get_weapon(player)
    base_dmg  = weapon.get("damage", 1) if weapon else 1
    weak_bonus = (
        WEAKNESS_BONUS_DAMAGE
        if weapon and weapon.get("weapon_type") in monster.get("weaknesses", [])
        else 0
    )
    avg_dice   = 3.5
    player_dpr = max(0, base_dmg + avg_dice + weak_bonus - monster["defense"])
    if player_dpr <= 0:
        return 0.0, 9999, 9999

    rounds_to_kill = monster["health"] / player_dpr
    armor          = total_armor(player)
    reduction      = min(ARMOR_REDUCTION_CAP, armor * ARMOR_REDUCTION_RATE)
    monster_dpr    = max(1, monster["damage"] * (1 - reduction))
    est_dmg_taken  = rounds_to_kill * monster_dpr

    # Factor in potions the player can use mid-fight
    potion_heal = sum(i.get("heal_amount", 0) for i in player.get("inventory", []) if i.get("heal_amount", 0) > 0)

    health_after = min(player["max_health"], player["health"] - est_dmg_taken + potion_heal)
    prob         = max(0.0, min(1.0, health_after / player["max_health"]))
    return prob, rounds_to_kill, est_dmg_taken


# ── BFS pathfinder ─────────────────────────────────────────────────────────────

def bfs_path(rooms, from_id, to_id, locked_exits_state, inventory_names):
    """Return list of room_ids from from_id to to_id (inclusive), or [] if unreachable."""
    from collections import deque
    visited = {from_id}
    queue   = deque([[from_id]])

    while queue:
        path = queue.popleft()
        node = path[-1]
        if node == to_id:
            return path

        room           = rooms[node]
        locked_state   = locked_exits_state.get(node, room.get("locked_exits", {}))

        for direction, dest in room.get("exits", {}).items():
            if dest in visited or dest not in rooms:
                continue
            lock = locked_state.get(direction, {})
            if lock.get("locked"):
                required = lock.get("required_key", "")
                if required not in inventory_names:
                    continue
            visited.add(dest)
            queue.append(path + [dest])

    return []


def reachable_rooms(rooms, from_id, locked_exits_state, inventory_names):
    """Return all room IDs reachable from from_id given current locks/inventory."""
    visited = {from_id}
    stack   = [from_id]
    while stack:
        node = stack.pop()
        room = rooms[node]
        locked_state = locked_exits_state.get(node, room.get("locked_exits", {}))
        for direction, dest in room.get("exits", {}).items():
            if dest in visited or dest not in rooms:
                continue
            lock = locked_state.get(direction, {})
            if lock.get("locked"):
                required = lock.get("required_key", "")
                if required not in inventory_names:
                    continue
            visited.add(dest)
            stack.append(dest)
    return visited


# ── SimBot ─────────────────────────────────────────────────────────────────────

class SimBot:
    """
    AI-controlled player. Strategies differ in combat aggression and heal thresholds.

    Strategy params:
        heal_threshold   — use potion when health < this fraction of max
        flee_threshold   — flee combat when health < this fraction of max (0 = never flee)
        win_prob_min     — minimum estimated win probability before engaging voluntarily
        npc_gifts        — True if the bot successfully triggers NPC gift conditions
    """

    STRATEGIES = {
        "aggressive": dict(heal_threshold=0.20, flee_threshold=0.0,  win_prob_min=0.0,  npc_gifts=True),
        "balanced":   dict(heal_threshold=0.40, flee_threshold=0.25, win_prob_min=0.25, npc_gifts=True),
        "cautious":   dict(heal_threshold=0.60, flee_threshold=0.45, win_prob_min=0.45, npc_gifts=True),
        "random":     dict(heal_threshold=0.30, flee_threshold=0.20, win_prob_min=0.15, npc_gifts=False),
    }

    def __init__(self, strategy: str, rooms: dict, item_cat: dict, npc_cat: dict, seed: int):
        cfg = self.STRATEGIES[strategy]
        self.strategy       = strategy
        self.heal_threshold = cfg["heal_threshold"]
        self.flee_threshold = cfg["flee_threshold"]
        self.win_prob_min   = cfg["win_prob_min"]
        self.npc_gifts      = cfg["npc_gifts"]

        self.rng        = random.Random(seed)
        self.base_rooms = rooms
        self.item_cat   = item_cat
        self.npc_cat    = npc_cat

        self.player = {
            "health": 100, "max_health": 100,
            "gold": 0, "inventory": [],
            "equipped_weapon": None, "equipped_armor": {},
        }
        self.current_room = "room_1"
        self.previous_room = None
        self.room_states  = {}   # {room_id: {items, monsters, locked_exits}}
        self.npc_gifts_collected = set()

        self.turn         = 0
        self.events       = []
        self.combat_log   = []
        self.rooms_visited = set()
        self.outcome      = None

    # ── State helpers ──────────────────────────────────────────────────────────

    def get_room(self, room_id=None):
        rid  = room_id or self.current_room
        base = self.base_rooms[rid]
        s    = self.room_states.get(rid, {})
        return {
            **base,
            "monsters":     s.get("monsters",     base["monsters"]),
            "items":        s.get("items",         base["items"]),
            "locked_exits": s.get("locked_exits",  base.get("locked_exits", {})),
        }

    def update_room(self, room_id, **kwargs):
        s = self.room_states.setdefault(room_id, {})
        s.update(kwargs)

    def inventory_names(self):
        return {i["name"] for i in self.player.get("inventory", [])}

    def has_item(self, name):
        return name in self.inventory_names()

    def log(self, msg, category="action"):
        self.events.append({
            "turn": self.turn, "room": self.current_room,
            "category": category, "msg": msg,
            "hp": self.player["health"],
        })

    def item_from_cat(self, item_id):
        entry = copy.deepcopy(self.item_cat.get(item_id, {}))
        return {**ITEM_DEFAULTS, **entry}

    # ── Main loop ──────────────────────────────────────────────────────────────

    def run(self, max_turns=MAX_TURNS):
        while self.turn < max_turns and self.outcome is None:
            self.turn += 1
            self.rooms_visited.add(self.current_room)

            # Check win condition first
            if self.current_room == RITUAL_ROOM and RITUAL_ITEMS.issubset(self.inventory_names()):
                self.outcome = "won"
                self.log("Performed the Ritual of Unmaking — escaped the mansion!", "win")
                break

            self._step()

        if self.outcome is None:
            self.outcome = "timeout"
            self.log(f"Simulation timeout after {self.turn} turns", "timeout")

        return self._build_report()

    def _step(self):
        room = self.get_room()

        # 1. Claim NPC gifts (iron key from Aldric, etc.)
        if self.npc_gifts:
            self._collect_npc_gifts(room)

        # 2. Equip upgrades immediately
        self._auto_equip()

        # 3. Open containers for gold
        self._open_containers(room)

        # 4. Heal if needed
        if self._should_heal():
            if self._use_potion():
                return

        # 5. Handle aggressive monster (forced combat)
        aggressive = [m for m in room["monsters"] if m.get("aggressive")]
        if aggressive:
            self._do_combat(aggressive[0])
            return

        # 6. Pick up useful items in current room
        for item in list(room["items"]):
            if not item.get("hidden") and self._want_item(item):
                self._take_item(item)
                return

        # 7. Reveal hidden items by examining triggers
        for item in list(room["items"]):
            if item.get("hidden") and item.get("revealed_by"):
                trigger_name = item["revealed_by"]
                if any(i["name"] == trigger_name for i in room["items"] if not i.get("hidden")):
                    self._examine_item(trigger_name, room)
                    return

        # 8. Voluntarily fight non-aggressive monsters blocking loot we want
        for monster in room["monsters"]:
            prob, _, _ = win_probability_estimate(self.player, monster)
            if self.strategy == "random":
                if self.rng.random() < 0.5:
                    self._do_combat(monster)
                    return
            elif prob >= self.win_prob_min:
                self._do_combat(monster)
                return

        # 9. Move toward next goal
        dest = self._choose_destination()
        if dest and dest != self.current_room:
            path = bfs_path(
                self.base_rooms, self.current_room, dest,
                {rid: s.get("locked_exits", {}) for rid, s in self.room_states.items()},
                self.inventory_names(),
            )
            if len(path) > 1:
                self._move_to(path[1])
                return

        # 10. No progress — mark stuck
        if not room["monsters"]:
            self.outcome = "stuck"
            self.log("Bot is stuck — no valid actions available", "stuck")

    # ── Actions ────────────────────────────────────────────────────────────────

    def _collect_npc_gifts(self, room):
        """Model NPC gift-giving: if NPC has a gift and bot hasn't received it yet, grant it."""
        for npc in room.get("npcs", []):
            npc_id   = next((k for k, v in self.npc_cat.items() if v["name"] == npc["name"]), None)
            gift_key = f"{npc_id}_gift"
            if gift_key in self.npc_gifts_collected:
                continue
            gift = npc.get("gift")
            if not gift or "item_name" not in gift:
                continue
            item_name = gift["item_name"]
            if self.has_item(item_name):
                continue
            # Find item data from catalogue (match by name)
            item_id   = next((k for k, v in self.item_cat.items() if v.get("name") == item_name), None)
            item_data = self.item_from_cat(item_id) if item_id else {**ITEM_DEFAULTS, "name": item_name}
            item_data["name"] = item_name
            self.player["inventory"].append(item_data)
            self.npc_gifts_collected.add(gift_key)
            self.log(f"Received gift from {npc['name']}: {item_name}", "gift")

    def _auto_equip(self):
        inventory = self.player.get("inventory", [])
        cur_weapon = get_weapon(self.player)
        cur_dmg    = cur_weapon.get("damage", 0) if cur_weapon else 0

        for item in inventory:
            if item.get("weapon_type") and item.get("damage", 0) > cur_dmg:
                self.player["equipped_weapon"] = item["name"]
                cur_dmg = item["damage"]
                self.log(f"Equipped weapon: {item['name']} ({item['damage']} dmg)", "equip")

        equipped_slots = dict(self.player.get("equipped_armor", {}))
        for item in inventory:
            slot = item.get("armor_slot")
            if not slot:
                continue
            current_name   = equipped_slots.get(slot)
            current_rating = 0
            if current_name:
                cur = next((i for i in inventory if i["name"] == current_name), None)
                current_rating = cur.get("armor_rating", 0) if cur else 0
            if item.get("armor_rating", 0) > current_rating:
                equipped_slots[slot] = item["name"]
                self.log(f"Equipped armor: {item['name']} ({slot}, {item['armor_rating']} rating)", "equip")

        self.player["equipped_armor"] = equipped_slots

    def _open_containers(self, room):
        for item in list(room["items"]):
            if item.get("openable") and not item.get("is_open"):
                gold = item.get("gold", 0)
                if gold > 0:
                    self.player["gold"] += gold
                    self.log(f"Opened {item['name']}: +{gold} gold", "loot")
                items_in_room = [i for i in room["items"] if i["name"] != item["name"]]
                items_in_room.append({**item, "is_open": True, "gold": 0})
                self.update_room(self.current_room, items=items_in_room)
                room = self.get_room()  # refresh

    def _should_heal(self):
        player = self.player
        if player["health"] >= player["max_health"]:
            return False
        threshold = player["max_health"] * self.heal_threshold
        if player["health"] >= threshold:
            return False
        return any(i.get("heal_amount", 0) > 0 for i in player.get("inventory", []))

    def _use_potion(self):
        inventory = self.player.get("inventory", [])
        potion = next((i for i in inventory if i.get("heal_amount", 0) > 0), None)
        if not potion:
            return False
        heal    = min(potion["heal_amount"], self.player["max_health"] - self.player["health"])
        self.player["health"]    += heal
        self.player["inventory"]  = [i for i in inventory if i != potion]
        self.log(f"Used {potion['name']}: +{heal} HP → {self.player['health']}/{self.player['max_health']}", "heal")
        return True

    def _want_item(self, item):
        name = item.get("name", "")
        if name in PRIORITY_ITEMS:
            return True
        if item.get("weapon_type"):
            cur = get_weapon(self.player)
            return item.get("damage", 0) > (cur.get("damage", 0) if cur else 0)
        if item.get("armor_slot"):
            slot    = item["armor_slot"]
            cur_name = self.player.get("equipped_armor", {}).get(slot)
            if not cur_name:
                return True
            cur = next((i for i in self.player.get("inventory", []) if i["name"] == cur_name), None)
            return item.get("armor_rating", 0) > (cur.get("armor_rating", 0) if cur else 0)
        if item.get("heal_amount", 0) > 0:
            return True
        if self.strategy == "random" and self.rng.random() < 0.4:
            return True
        return False

    def _take_item(self, item):
        room = self.get_room()
        new_items = [i for i in room["items"] if i["name"] != item["name"]]
        self.update_room(self.current_room, items=new_items)
        self.player["inventory"].append(copy.deepcopy(item))
        self.log(f"Took: {item['name']}", "take")

    def _examine_item(self, trigger_name, room):
        hidden = [i for i in room["items"] if i.get("hidden") and i.get("revealed_by") == trigger_name]
        if not hidden:
            return
        remaining = [i for i in room["items"] if not (i.get("hidden") and i.get("revealed_by") == trigger_name)]
        self.update_room(self.current_room, items=remaining)
        for item in hidden:
            self.player["inventory"].append(copy.deepcopy(item))
            self.log(f"Revealed and took: {item['name']} (from {trigger_name})", "examine")

    def _best_weapon_for(self, monster) -> dict | None:
        return best_weapon_for(self.player, monster)

    def _do_combat(self, monster):
        monster     = copy.deepcopy(monster)
        m_name      = monster["name"]
        room_id     = self.current_room
        room        = self.get_room()
        hp_before   = self.player["health"]
        weapon      = self._best_weapon_for(monster) or get_weapon(self.player)
        weapon_name = weapon["name"] if weapon else "bare hands"

        rounds          = 0
        total_dealt     = 0
        total_taken     = 0
        outcome         = None
        flee_attempts   = 0

        while monster["health"] > 0 and self.player["health"] > 0:
            rounds += 1

            # Flee check (non-aggressive won't force combat, but aggressive monsters do)
            if self.flee_threshold > 0 and rounds > 1:
                if self.player["health"] < self.player["max_health"] * self.flee_threshold:
                    if self.strategy == "random" and self.rng.random() > 0.5:
                        pass  # random: sometimes don't flee
                    else:
                        flee_roll = self.rng.randint(1, 100)
                        flee_attempts += 1
                        if flee_roll > FLEE_SUCCESS_THRESHOLD:
                            # Fled successfully
                            outcome = "fled"
                            break
                        else:
                            # Failed flee — monster hits
                            variance  = monster.get("damage_variance", 2)
                            armor_r   = total_armor(self.player)
                            raw_dmg   = monster["damage"] + self.rng.randint(-variance, variance)
                            reduction = min(ARMOR_REDUCTION_CAP, armor_r * ARMOR_REDUCTION_RATE)
                            m_dmg     = max(1, int(raw_dmg * (1 - reduction)))
                            self.player["health"] -= m_dmg
                            total_taken += m_dmg
                            if self.player["health"] <= 0:
                                outcome = "died"
                                break
                            continue

            # Player attacks
            base_dmg   = weapon.get("damage", 1) if weapon else 1
            dice       = self.rng.randint(1, 6)
            weak_bonus = (
                WEAKNESS_BONUS_DAMAGE
                if weapon and weapon.get("weapon_type") in monster.get("weaknesses", [])
                else 0
            )
            p_dmg = max(0, base_dmg + dice + weak_bonus - monster["defense"])
            monster["health"] -= p_dmg
            total_dealt += p_dmg

            if monster["health"] <= 0:
                outcome = "won"
                break

            # Monster retaliates
            variance  = monster.get("damage_variance", 2)
            armor_r   = total_armor(self.player)
            raw_dmg   = monster["damage"] + self.rng.randint(-variance, variance)
            reduction = min(ARMOR_REDUCTION_CAP, armor_r * ARMOR_REDUCTION_RATE)
            m_dmg     = max(1, int(raw_dmg * (1 - reduction)))
            self.player["health"] -= m_dmg
            total_taken += m_dmg

            if self.player["health"] <= 0:
                outcome = "died"
                break

        # Record
        result = {
            "run_turn":        self.turn,
            "room":            room_id,
            "monster":         m_name,
            "outcome":         outcome,
            "rounds":          rounds,
            "player_dmg":      total_dealt,
            "monster_dmg":     total_taken,
            "hp_before":       hp_before,
            "hp_after":        self.player["health"],
            "weapon":          weapon_name,
            "weakness_hit":    weak_bonus > 0,
            "flee_attempts":   flee_attempts,
        }
        self.combat_log.append(result)
        self.log(
            f"Combat vs {m_name}: {outcome} in {rounds}r | dealt {total_dealt}, took {total_taken} | hp {hp_before}→{self.player['health']}",
            "combat"
        )

        if outcome == "won":
            self._handle_victory(monster, room_id)
        elif outcome == "died":
            self.outcome = "died"
            self.log(f"Killed by {m_name}", "death")
        elif outcome == "fled":
            dest = self.previous_room or "room_1"
            self.previous_room = self.current_room
            self.current_room  = dest
            self.log(f"Fled from {m_name} → {dest}", "flee")
            # Persist the monster's damaged health so the next fight continues from here
            cur_room = self.get_room(room_id)
            updated_monsters = [
                {**m, "health": max(0, monster["health"])} if m["name"] == m_name else m
                for m in cur_room["monsters"]
            ]
            self.update_room(room_id, monsters=updated_monsters)

    def _handle_victory(self, monster, room_id):
        room = self.get_room(room_id)
        remaining = [m for m in room["monsters"] if m["name"] != monster["name"]]
        self.update_room(room_id, monsters=remaining)

        drops = monster.get("drops", {})
        gold  = drops.get("gold", 0)
        item_id = drops.get("item")

        if gold:
            self.player["gold"] += gold
            self.log(f"Looted {gold} gold from {monster['name']}", "loot")

        if item_id:
            item_data = self.item_from_cat(item_id)
            if not item_data.get("name"):
                item_data["name"] = item_id.replace("_", " ")
            self.player["inventory"].append(item_data)
            self.log(f"Looted {item_data['name']} from {monster['name']}", "loot")

    def _move_to(self, room_id):
        room = self.get_room()
        # Unlock if we have the key
        locked = room.get("locked_exits", {})
        for direction, dest in room.get("exits", {}).items():
            if dest == room_id:
                lock = locked.get(direction, {})
                if lock.get("locked"):
                    req = lock.get("required_key", "")
                    if req in self.inventory_names():
                        new_locked = {**locked, direction: {**lock, "locked": False}}
                        self.update_room(self.current_room, locked_exits=new_locked)
                        self.log(f"Unlocked {direction} exit with {req}", "unlock")

        self.previous_room = self.current_room
        self.current_room  = room_id
        self.log(f"Moved to {room_id} ({self.base_rooms[room_id]['name']})", "move")

    def _path_is_safe(self, dest_id, locks_state, inv_names):
        """True if BFS path to dest doesn't pass through a room with a dangerous aggressive monster."""
        path = bfs_path(self.base_rooms, self.current_room, dest_id, locks_state, inv_names)
        if not path:
            return False
        for room_id in path[1:]:
            room = self.get_room(room_id)
            for monster in room["monsters"]:
                if monster.get("aggressive"):
                    prob, _, _ = win_probability_estimate(self.player, monster)
                    if prob < self.win_prob_min:
                        return False
        return True

    def _choose_destination(self):
        """Return the room_id the bot most wants to go to next."""
        inv_names   = self.inventory_names()
        locks_state = {rid: s.get("locked_exits", {}) for rid, s in self.room_states.items()}
        accessible  = reachable_rooms(self.base_rooms, self.current_room, locks_state, inv_names)

        # Priority 1: ritual room if all components ready
        if RITUAL_ITEMS.issubset(inv_names) and RITUAL_ROOM in accessible:
            return RITUAL_ROOM

        # Priority 2: rooms with ritual components — safe paths first, then unsafe
        # A room only qualifies if the bot can actually acquire the missing item:
        # floor items are always available; monster drops require a winnable fight.
        missing_ritual = RITUAL_ITEMS - inv_names
        safe_ritual, unsafe_ritual = [], []
        for room_id in accessible:
            if room_id == self.current_room:
                continue
            room = self.get_room(room_id)
            room_item_names = {i["name"] for i in room["items"] if not i.get("hidden")}
            for m in room["monsters"]:
                drop_id = m.get("drops", {}).get("item")
                if drop_id:
                    di = self.item_cat.get(drop_id, {})
                    drop_name = di.get("name", drop_id.replace("_", " "))
                    if drop_name in missing_ritual:
                        # Only count this drop if the bot can feasibly win the fight
                        prob, _, _ = win_probability_estimate(self.player, m)
                        if prob >= self.win_prob_min:
                            room_item_names.add(drop_name)
                    else:
                        room_item_names.add(drop_name)
            if room_item_names & missing_ritual:
                if self._path_is_safe(room_id, locks_state, inv_names):
                    safe_ritual.append(room_id)
                else:
                    unsafe_ritual.append(room_id)

        if safe_ritual:
            return safe_ritual[0]

        # Priority 3: unvisited rooms with useful items — safe paths first
        safe_unvisited = []
        for room_id in accessible:
            if room_id in self.rooms_visited or room_id == self.current_room:
                continue
            if self._path_is_safe(room_id, locks_state, inv_names):
                safe_unvisited.append(room_id)
        if safe_unvisited:
            return safe_unvisited[0]

        # Priority 4: unvisited rooms even via dangerous paths
        for room_id in accessible:
            if room_id not in self.rooms_visited and room_id != self.current_room:
                return room_id

        # Priority 5: unsafe ritual rooms (bot has explored everything safe — time to commit)
        if unsafe_ritual:
            return unsafe_ritual[0]

        # Priority 6: closest unvisited anywhere
        all_rooms = reachable_rooms(self.base_rooms, "room_1", {}, set())
        unvisited = all_rooms - self.rooms_visited - {self.current_room}
        if unvisited:
            best, best_len = None, 9999
            for uid in unvisited:
                path = bfs_path(self.base_rooms, self.current_room, uid, locks_state, inv_names)
                if 0 < len(path) < best_len:
                    best, best_len = uid, len(path)
            if best:
                return best

        return None

    # ── Report builder ─────────────────────────────────────────────────────────

    def _build_report(self):
        inv_names = self.inventory_names()
        all_rooms  = set(self.base_rooms.keys())

        # Items across the whole game world
        all_items_in_world = set()
        for room in self.base_rooms.values():
            for item in room.get("items", []):
                all_items_in_world.add(item.get("name", ""))
            for m in room.get("monsters", []):
                drop_id = m.get("drops", {}).get("item")
                if drop_id:
                    di = self.item_cat.get(drop_id, {})
                    all_items_in_world.add(di.get("name", drop_id.replace("_", " ")))

        ritual_found = [r for r in RITUAL_ITEMS if r in inv_names]
        ritual_missing = [r for r in RITUAL_ITEMS if r not in inv_names]

        return {
            "strategy":            self.strategy,
            "outcome":             self.outcome,
            "turns":               self.turn,
            "final_hp":            self.player["health"],
            "final_gold":          self.player["gold"],
            "rooms_visited":       sorted(self.rooms_visited),
            "rooms_visited_count": len(self.rooms_visited),
            "rooms_missed":        sorted(all_rooms - self.rooms_visited),
            "rooms_total":         len(all_rooms),
            "ritual_components_found":   ritual_found,
            "ritual_components_missing": ritual_missing,
            "inventory_final":     [i["name"] for i in self.player.get("inventory", [])],
            "items_collected_count": len(inv_names),
            "items_missed":        sorted(all_items_in_world - inv_names - {""}),
            "equipped_weapon":     self.player.get("equipped_weapon"),
            "equipped_armor":      self.player.get("equipped_armor", {}),
            "combat_log":          self.combat_log,
            "combat_count":        len(self.combat_log),
            "combats_won":         sum(1 for c in self.combat_log if c["outcome"] == "won"),
            "combats_died":        sum(1 for c in self.combat_log if c["outcome"] == "died"),
            "combats_fled":        sum(1 for c in self.combat_log if c["outcome"] == "fled"),
            "total_damage_taken":  sum(c["monster_dmg"] for c in self.combat_log),
            "total_damage_dealt":  sum(c["player_dmg"]  for c in self.combat_log),
            "death_cause":         next(
                (c["monster"] for c in self.combat_log if c["outcome"] == "died"), None
            ),
            "event_log":           self.events,
        }


# ── Analysis ───────────────────────────────────────────────────────────────────

def analyse(all_runs: list[dict]) -> dict:
    by_strategy = defaultdict(list)
    for r in all_runs:
        by_strategy[r["strategy"]].append(r)

    strategy_summary = {}
    for strat, runs in by_strategy.items():
        won    = [r for r in runs if r["outcome"] == "won"]
        died   = [r for r in runs if r["outcome"] == "died"]
        stuck  = [r for r in runs if r["outcome"] == "stuck"]
        timeout= [r for r in runs if r["outcome"] == "timeout"]
        n      = len(runs)

        death_causes = defaultdict(int)
        for r in died:
            if r.get("death_cause"):
                death_causes[r["death_cause"]] += 1

        ritual_rates = {item: sum(1 for r in runs if item in r["ritual_components_found"]) / n
                        for item in RITUAL_ITEMS}

        avg_turns_won  = sum(r["turns"] for r in won)  / len(won)  if won  else None
        avg_turns_died = sum(r["turns"] for r in died) / len(died) if died else None

        # Per-monster stats across all runs
        monster_stats = defaultdict(lambda: {"fights": 0, "wins": 0, "deaths": 0, "flees": 0,
                                              "avg_dmg_taken": [], "avg_rounds": []})
        for r in runs:
            for c in r["combat_log"]:
                ms = monster_stats[c["monster"]]
                ms["fights"] += 1
                ms["wins"]   += c["outcome"] == "won"
                ms["deaths"] += c["outcome"] == "died"
                ms["flees"]  += c["outcome"] == "fled"
                ms["avg_dmg_taken"].append(c["monster_dmg"])
                ms["avg_rounds"].append(c["rounds"])

        monster_summary = {}
        for m, ms in monster_stats.items():
            fights = ms["fights"]
            monster_summary[m] = {
                "fights":        fights,
                "win_rate":      round(ms["wins"]   / fights, 3),
                "death_rate":    round(ms["deaths"] / fights, 3),
                "flee_rate":     round(ms["flees"]  / fights, 3),
                "avg_dmg_taken": round(sum(ms["avg_dmg_taken"]) / len(ms["avg_dmg_taken"]), 1) if ms["avg_dmg_taken"] else 0,
                "avg_rounds":    round(sum(ms["avg_rounds"])    / len(ms["avg_rounds"]),    1) if ms["avg_rounds"]    else 0,
            }

        rooms_visit_rates = {}
        all_room_ids = set()
        for r in runs:
            all_room_ids |= set(r["rooms_visited"] + r["rooms_missed"])
        for rid in all_room_ids:
            rooms_visit_rates[rid] = round(sum(1 for r in runs if rid in r["rooms_visited"]) / n, 3)

        strategy_summary[strat] = {
            "runs":           n,
            "win_rate":       round(len(won)    / n, 3),
            "death_rate":     round(len(died)   / n, 3),
            "stuck_rate":     round(len(stuck)  / n, 3),
            "timeout_rate":   round(len(timeout)/ n, 3),
            "avg_turns_won":  round(avg_turns_won,  1) if avg_turns_won  is not None else None,
            "avg_turns_died": round(avg_turns_died, 1) if avg_turns_died is not None else None,
            "avg_final_hp":   round(sum(r["final_hp"]   for r in runs) / n, 1),
            "avg_gold":       round(sum(r["final_gold"] for r in runs) / n, 1),
            "death_causes":   dict(sorted(death_causes.items(), key=lambda x: -x[1])),
            "ritual_component_rates": {k: round(v, 3) for k, v in ritual_rates.items()},
            "monster_stats":  dict(sorted(monster_summary.items(), key=lambda x: -x[1]["death_rate"])),
            "room_visit_rates": dict(sorted(rooms_visit_rates.items())),
        }

    # Balance flags — heuristic issues to highlight
    balance_issues = []

    for strat, summary in strategy_summary.items():
        win_rate = summary["win_rate"]
        if win_rate == 0:
            balance_issues.append({
                "severity": "critical",
                "issue": f"[{strat}] Win rate is 0% — game may be unwinnable for this play style",
            })
        elif win_rate < 0.15:
            balance_issues.append({
                "severity": "high",
                "issue": f"[{strat}] Win rate is very low ({win_rate:.0%}) — game may be too difficult",
            })

        for monster, ms in summary["monster_stats"].items():
            if ms["death_rate"] > 0.5:
                balance_issues.append({
                    "severity": "high",
                    "issue": f"[{strat}] {monster}: death rate {ms['death_rate']:.0%} — likely too powerful or encountered too early",
                    "avg_dmg_taken": ms["avg_dmg_taken"],
                    "avg_rounds": ms["avg_rounds"],
                })
            elif ms["death_rate"] > 0.25:
                balance_issues.append({
                    "severity": "medium",
                    "issue": f"[{strat}] {monster}: death rate {ms['death_rate']:.0%} — challenging but possibly intentional",
                })

        for item, rate in summary["ritual_component_rates"].items():
            if rate < 0.5:
                balance_issues.append({
                    "severity": "high",
                    "issue": f"[{strat}] Ritual component '{item}' only found in {rate:.0%} of runs — may be inaccessible or too hard to obtain",
                })

        for room_id, rate in summary["room_visit_rates"].items():
            if rate < 0.1:
                balance_issues.append({
                    "severity": "medium",
                    "issue": f"[{strat}] Room {room_id} visited in only {rate:.0%} of runs — possibly unreachable or players avoid it",
                })

    return {
        "by_strategy": strategy_summary,
        "balance_issues": sorted(balance_issues, key=lambda x: {"critical": 0, "high": 1, "medium": 2}.get(x["severity"], 3)),
    }


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Haunted Mansion game simulator")
    parser.add_argument("--runs",       type=int,   default=20,
                        help="Simulations per strategy (default: 20)")
    parser.add_argument("--strategies", nargs="+",
                        default=["aggressive", "balanced", "cautious", "random"],
                        choices=["aggressive", "balanced", "cautious", "random"],
                        help="Strategies to test")
    parser.add_argument("--output",     type=str,   default=None,
                        help="Output filename (default: sim_TIMESTAMP.json)")
    parser.add_argument("--seed",       type=int,   default=42,
                        help="Base random seed (each run increments this)")
    parser.add_argument("--no-events",  action="store_true",
                        help="Omit per-turn event logs from output (smaller file)")
    args = parser.parse_args()

    print(f"Loading game data...")
    rooms, item_cat, monster_cat, npc_cat = load_game_data()
    print(f"  {len(rooms)} rooms, {len(item_cat)} items, {len(monster_cat)} monsters, {len(npc_cat)} NPCs")

    all_runs   = []
    total_runs = len(args.strategies) * args.runs
    completed  = 0

    for strategy in args.strategies:
        wins = deaths = 0
        for i in range(args.runs):
            seed = args.seed + completed
            bot  = SimBot(strategy, rooms, item_cat, npc_cat, seed)
            run  = bot.run()

            if args.no_events:
                run.pop("event_log", None)

            all_runs.append(run)
            completed += 1
            if run["outcome"] == "won":    wins   += 1
            elif run["outcome"] == "died": deaths += 1

            # Progress
            sys.stdout.write(
                f"\r  {completed}/{total_runs}  "
                f"{strategy}: wins={wins} deaths={deaths}    "
            )
            sys.stdout.flush()

        print()

    print("\nAnalysing results...")
    analysis = analyse(all_runs)

    output = {
        "meta": {
            "timestamp":       datetime.now().isoformat(),
            "game_data": {
                "rooms":    len(rooms),
                "items":    len(item_cat),
                "monsters": len(monster_cat),
                "npcs":     len(npc_cat),
            },
            "strategies_tested": args.strategies,
            "runs_per_strategy": args.runs,
            "total_runs":        total_runs,
            "max_turns":         MAX_TURNS,
            "ritual_items":      sorted(RITUAL_ITEMS),
            "ritual_room":       RITUAL_ROOM,
        },
        "analysis": analysis,
        "runs":     all_runs,
    }

    filename = args.output or f"sim_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nResults written to: {filename}")

    # Print quick summary to console
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for strat, s in analysis["by_strategy"].items():
        print(f"\n{strat.upper()}")
        print(f"  Win rate:    {s['win_rate']:.0%}   ({s['runs']} runs)")
        print(f"  Death rate:  {s['death_rate']:.0%}")
        print(f"  Avg turns (win):  {s['avg_turns_won']}")
        print(f"  Avg turns (die):  {s['avg_turns_died']}")
        if s["death_causes"]:
            causes = ", ".join(f"{k} x{v}" for k, v in list(s["death_causes"].items())[:3])
            print(f"  Death causes: {causes}")
        ritual = s["ritual_component_rates"]
        print(f"  Ritual: " + " | ".join(f"{k}: {v:.0%}" for k, v in ritual.items()))

    issues = analysis["balance_issues"]
    if issues:
        print(f"\nBALANCE ISSUES ({len(issues)} found)")
        print("-" * 60)
        sev_labels = {"critical": "[CRIT]", "high": "[HIGH]", "medium": "[MED ]"}
        for issue in issues[:15]:
            label = sev_labels.get(issue["severity"], "[    ]")
            print(f"  {label} {issue['issue']}")
        if len(issues) > 15:
            print(f"  ... and {len(issues) - 15} more (see JSON)")
    else:
        print("\nNo balance issues detected.")

    print(f"\nFull report: {filename}")


if __name__ == "__main__":
    main()
