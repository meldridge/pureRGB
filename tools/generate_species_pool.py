#!/usr/bin/env python3
"""Generate data/randomizer/species_pool.asm.

The randomizer shuffles internal species indices, but not every index is a real
Pokemon: pureRGB adds variants (HARDENED_ONIX, ARMORED_MEWTWO, the spirits, ...)
plus MissingNo and the sprite-only fossil pseudo-mons. Shuffling those in would
spawn MissingNo and corrupt saves.

Rather than hand-maintaining a blocklist, we exploit the fact that every variant
aliases onto the dex number of the species it is a variant of. So for each dex
number we keep exactly one internal index -- the one whose constant name matches
the dex constant name -- and drop the rest. NO_MON and DEX_MISSINGNO are dropped
outright.

The surviving pool is sorted by base-stat total, and each entry records the range
of positions it may be exchanged with, so the runtime shuffle keeps replacements
roughly power-matched.

Starters are not shuffled. They come from a fixed list of sets, mostly generated
here as type cycles -- A beats B beats C beats A -- plus a few hand-written ones.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Replacements must be within this fraction of the original's base stat total,
# measured against the smaller of the two so the relation is symmetric.
BST_TOLERANCE = 0.10

# The tolerance is proportional, so at the ends of the range it reaches very few
# species: unwidened, the five weakest only ever swap among each other, and once
# the level rule rules out the ones that evolve, Caterpie is left with three
# candidates. Widening applies only where the tolerance falls short of this, so
# the middle of the pool is untouched.
MIN_WINDOW = 16

# Dex numbers the Universal Pokemon Randomizer treats as legendary. Kept out of
# the pool entirely, so they are never handed out and never replaced.
LEGENDARY_DEX = {144, 145, 146, 150, 151}

CONSTANTS = ROOT / "constants/pokemon_constants.asm"
DEX_ORDER = ROOT / "data/pokemon/dex_order.asm"
BASE_STATS = ROOT / "data/pokemon/base_stats.asm"
EVOS_MOVES = ROOT / "data/pokemon/evos_moves.asm"
WILD_MAPS = ROOT / "data/wild/maps"
ROD_TABLES = (ROOT / "data/wild/good_rod.asm", ROOT / "data/wild/super_rod.asm")
ITEM_ASSIGNMENTS = ROOT / "data/maps/items/item_location_assignments.asm"
KEY_ITEMS = ROOT / "data/items/key_items.asm"
TYPE_MATCHUPS = ROOT / "data/types/type_matchups.asm"
OUT = ROOT / "data/randomizer/species_pool.asm"
OUT_CONSTANTS = ROOT / "constants/randomizer_constants.asm"

# How many starter sets to emit, hand-written ones included.
TOTAL_STARTER_SETS = 151

# Allowed as starters despite being evolutions.
EEVEELUTIONS = {"FLAREON", "VAPOREON", "JOLTEON"}

# Transform is all it ever knows, so it cannot function as a sole starter.
UNPLAYABLE_STARTERS = {"DITTO"}

# Exempt from the cycle rule and from the legendary exclusion.
STARTER_SET_PIECES = [
    ("Eeveelutions", ("FLAREON", "VAPOREON", "JOLTEON")),
    ("Legendary birds", ("MOLTRES", "ARTICUNO", "ZAPDOS")),
    ("Vanilla", ("CHARMANDER", "SQUIRTLE", "BULBASAUR")),
    ("Team Rocket", ("EKANS", "KOFFING", "MEOWTH")),
    ("Fossils", ("OMANYTE", "KABUTO", "AERODACTYL")),
    ("Titans", ("LAPRAS", "SNORLAX", "AERODACTYL")),
    ("Hard mode", ("MAGIKARP", "CATERPIE", "WEEDLE")),
]

EFFECTIVENESS = {
    "SUPER_EFFECTIVE": 2.0,
    "NOT_VERY_EFFECTIVE": 0.5,
    "NO_EFFECT": 0.0,
}


def parse_species_constants():
    """Internal index -> species constant name, from the const_def list."""
    names = {}
    index = None
    for line in CONSTANTS.read_text().splitlines():
        line = line.split(";")[0].strip()
        if line.startswith("const_def"):
            index = 0
            continue
        if index is None:
            continue
        m = re.match(r"^const\s+([A-Z0-9_]+)$", line)
        if m:
            names[index] = m.group(1)
            index += 1
            continue
        m = re.match(r"^const_skip\s*(\d*)$", line)
        if m:
            index += int(m.group(1) or 1)
            continue
        if line.startswith("DEF NUM_POKEMON_INDEXES"):
            break
    return names


def parse_dex_order():
    """Internal index -> DEX_ constant name (index 1 is the first db).

    Unused internal indices are written as a bare `db 0` rather than
    `db DEX_MISSINGNO`, and they still occupy a slot -- skipping them would
    shift every index after them.
    """
    order = {}
    index = 1
    for line in DEX_ORDER.read_text().splitlines():
        line = line.split(";")[0].strip()
        m = re.match(r"^db\s+(DEX_[A-Z0-9_]+|0)$", line)
        if m:
            value = m.group(1)
            order[index] = "DEX_MISSINGNO" if value == "0" else value
            index += 1
    return order


def parse_base_stat_files():
    """Dex number -> base stats file path, from the dex-ordered INCLUDE list.

    Only the includes before NonDexMonsBaseStats are dex-ordered; the ones after
    it are the pureRGB specials and are indexed separately.
    """
    files = {}
    dex = 1
    for line in BASE_STATS.read_text().splitlines():
        if "NonDexMonsBaseStats" in line:
            break
        m = re.match(r'^INCLUDE\s+"(.*base_stats/.*\.asm)"', line.strip())
        if m:
            files[dex] = ROOT / m.group(1)
            dex += 1
    return files


# Maps reachable before Surf is needed. A seed is only unwinnable if nothing
# catchable across all of these can learn the field moves.
EARLY_MAPS = [
    "Route1", "Route2", "ViridianForest", "Route22", "Route3",
    "MtMoon1F", "MtMoonB1F", "MtMoonB2F", "Route4", "Route24", "Route25",
    "Route5", "Route6", "Route7", "Route8", "Route9", "Route10",
    "RockTunnel1F", "RockTunnelB1F", "Route11", "DiglettsCave",
    "Route12", "Route13", "Route14", "Route15", "Route16", "Route17", "Route18",
]

# bit positions in the emitted RandoHmLearners table
HM_MOVES = ["CUT", "SURF", "STRENGTH"]


def parse_hm_learners(stat_files, names_to_index):
    """Species internal index -> bitmask of which field moves it can learn."""
    learners = {}
    for dex, path in stat_files.items():
        text = path.read_text()
        m = re.search(r"^\s*db\s+(DEX_[A-Z0-9_]+)", text, re.M)
        if not m:
            continue
        species_name = m.group(1)[len("DEX_"):]
        index = names_to_index.get(species_name)
        if index is None:
            continue
        # the tmhm macro takes a backslash continued list of move names
        block = re.search(r"tmhm\s*\\(.*?)\n\s*;\s*end", text, re.S)
        body = block.group(1) if block else ""
        moves = {t.strip().rstrip(",\\").strip() for t in body.split()}
        bits = 0
        for i, move in enumerate(HM_MOVES):
            if move in moves:
                bits |= 1 << i
        learners[index] = bits
    return learners


def parse_early_species(names_to_index):
    """Internal indexes appearing in the wild data of the early maps."""
    found = set()
    for name in EARLY_MAPS:
        path = ROOT / f"data/wild/maps/{name}.asm"
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.split(";")[0].strip()
            m = re.match(r"^db\s+\d+\s*,\s*([A-Z0-9_]+)\s*$", line)
            if m and m.group(1) in names_to_index:
                found.add(names_to_index[m.group(1)])
    return found


def flatten(name):
    """Evos/moves labels drop the underscores that species constants carry."""
    return name.replace("_", "")


def parse_evolutions():
    """Species constant name -> the species it evolves into, or None.

    Only the first evolution is taken, whatever its method: a stone or trade
    evolution is fine for the rival's starter. Records share tails --
    RhyhornEvosMoves falls through into RhydonEvosMoves -- so read from a label
    to the first terminator, ignoring any label lines in between.
    """
    evolves, current = {}, None
    for line in EVOS_MOVES.read_text().splitlines():
        line = line.split(";")[0].strip()
        m = re.match(r"^(\w+)EvosMoves:", line)
        if m:
            current = m.group(1).upper()
            continue
        if current is None:
            continue
        if line.startswith("db EVOLVE"):
            evolves.setdefault(current, line.split(",")[-1].strip())
        elif line == "db 0":
            evolves.setdefault(current, None)
            current = None
    return evolves


def parse_evolution_levels(flat_to_name):
    """(pre-evolution, evolved form, level) for every evolution route.

    Item and trade evolutions carry a min level of 1, so they contribute no
    level of their own. A species can have more than one route in -- pureRGB
    gives Gengar a level 37 alternative to the trade -- and each is its own edge.
    """
    edges, current = [], None
    for line in EVOS_MOVES.read_text().splitlines():
        line = line.split(";")[0].strip()
        m = re.match(r"^(\w+)EvosMoves:", line)
        if m:
            current = flat_to_name.get(m.group(1).upper())
            continue
        if current is None:
            continue
        if line.startswith("db EVOLVE_LEVEL"):
            fields = [f.strip() for f in line.split(",")]
            edges.append((current, fields[-1], int(fields[1])))
        elif line.startswith("db EVOLVE"):
            edges.append((current, line.split(",")[-1].strip(), 1))
        elif line == "db 0":
            current = None
    return edges


def compute_min_levels(edges, names):
    """Species constant -> the earliest level it could legitimately be held at.

    A base form is 1. An evolved form is the higher of its pre-evolution's floor
    and its own evolution level, minimised over every route in. Iterated to a
    fixed point, since one stage's floor depends on the stage before it.
    """
    routes = {}
    for src, dst, level in edges:
        routes.setdefault(dst, []).append((src, level))
    min_level = {name: 1 for name in names}
    for _ in range(len(min_level)):
        changed = False
        for dst, ways in routes.items():
            if dst not in min_level:
                continue
            best = min(max(min_level.get(src, 1), lvl) for src, lvl in ways)
            if best > min_level[dst]:
                min_level[dst] = best
                changed = True
        if not changed:
            break
    return min_level


def strip_debug(text):
    """Drop the _DEBUG arm of a conditional so only the real data is read."""
    out, skip = [], False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("IF DEF(_DEBUG)"):
            skip = True
        elif stripped in ("ELSE", "ENDC"):
            skip = False
        elif not skip:
            out.append(line)
    return "\n".join(out)


def parse_encounter_levels():
    """Species constant -> the lowest level it can be caught at.

    Grass, water and both rods. Trainer parties are deliberately absent: their
    mons are fought rather than obtained, so nothing there has to be a level the
    player could legitimately hold. Statics and gifts set their level in scripts
    and are not read; each sits at or above the wild floor for the same species,
    so none would lower a cap.
    """
    wild = {}

    def note(species, level):
        if 0 < level <= 100 and level < wild.get(species, 101):
            wild[species] = level

    for path in sorted(WILD_MAPS.glob("*.asm")):
        text = strip_debug(path.read_text())
        for kind in ("grass", "water"):
            block = r"def_%s_wildmons (\d+)(.*?)end_%s_wildmons" % (kind, kind)
            for m in re.finditer(block, text, re.S):
                if int(m.group(1)) == 0:
                    continue  # encounter rate zero, so these slots never come up
                for level, species in re.findall(r"db\s+(\d+)\s*,\s*(\w+)", m.group(2)):
                    note(species, int(level))

    for path in ROD_TABLES:
        for level, species in re.findall(r"db\s+(\d+)\s*,\s*(\w+)", path.read_text()):
            note(species, int(level))

    return wild


def parse_key_items():
    """Item constants flagged as key items."""
    return {m.group(2) for m in
            (re.match(r"\s*dbit\s+(TRUE|FALSE)\s*;\s*(\w+)", line)
             for line in KEY_ITEMS.read_text().splitlines())
            if m and m.group(1) == "TRUE"}


def build_item_pool():
    """Items a randomized ground or hidden item may turn into.

    Read from the location table pureRGB already maintains, so adding a field
    item to the game adds it here too. Key items are excluded because several of
    them -- the Silph Scope, Lift Key, Secret Key and Gold Teeth -- are ordinary
    item balls, and moving those can leave a seed unwinnable. HMs go for the
    same reason, and TMs because the TM setting owns them.
    """
    assigned = re.findall(r"^DEF\s+ITEM_\w+\s+EQU\s+(\w+)",
                          ITEM_ASSIGNMENTS.read_text(), re.M)
    keys = parse_key_items()
    pool = {item for item in assigned
            if item not in keys
            and not re.match(r"^(TM|HM)\d", item)}
    return sorted(pool)


def parse_species_types(stat_files):
    """Species constant name -> its two type constants."""
    types = {}
    for path in stat_files.values():
        text = path.read_text()
        dex = re.search(r"db\s+(DEX_[A-Z0-9_]+)", text)
        ty = re.search(r"db\s+([A-Z_]+)\s*,\s*([A-Z_]+)\s*;\s*type", text)
        if dex and ty:
            types[dex.group(1)[len("DEX_"):]] = (ty.group(1), ty.group(2))
    return types


def parse_type_chart():
    """(attacking type, defending type) -> multiplier, for the listed pairs."""
    chart = {}
    for line in TYPE_MATCHUPS.read_text().splitlines():
        m = re.match(r"\s*db\s+([A-Z_]+)\s*,\s*([A-Z_]+)\s*,\s*([A-Z_]+)", line)
        if m and m.group(3) in EFFECTIVENESS:
            chart[(m.group(1), m.group(2))] = EFFECTIVENESS[m.group(3)]
    return chart


def make_beats(types, chart):
    """A beats B when its better type is super effective on B's full typing.

    Effectiveness multiplies across both of the defender's types, so this cannot
    be decided on single types alone: Grass is 2x on Rock but 0.5x on Flying, so
    a Grass attacker is exactly neutral into Rock/Flying.
    """
    def effectiveness(attacking, defender):
        product = 1.0
        for defending in set(types[defender]):
            product *= chart.get((attacking, defending), 1.0)
        return product

    def beats(a, b):
        return max(effectiveness(t, b) for t in set(types[a])) > 1.0

    return beats


def build_starter_sets(types, evolves, legendaries, print_report):
    """The fixed list of starter sets: hand-written ones, then type cycles.

    Sets are chosen so no pair of species appears in two of them, which stops
    the list filling up with near-duplicates.
    """
    evolved = {flatten(v) for v in evolves.values() if v}
    candidates = sorted(
        s for s in types
        if s not in UNPLAYABLE_STARTERS
        and s not in legendaries
        and (flatten(s) not in evolved or s in EEVEELUTIONS)
    )
    beats = make_beats(types, parse_type_chart())
    wins = {a: {b for b in candidates if b != a and beats(a, b)} for a in candidates}
    cycles = [(a, b, c) for a in candidates for b in sorted(wins[a])
              for c in sorted(wins[b]) if c != a and a in wins[c]]

    used_pairs, uses, sets = set(), {}, []

    def take(members, label):
        for i in range(3):
            for j in range(i + 1, 3):
                used_pairs.add(frozenset((members[i], members[j])))
        for s in members:
            uses[s] = uses.get(s, 0) + 1
        sets.append((label, members))

    for label, members in STARTER_SET_PIECES:
        take(members, label)

    def free(triple):
        return not any(frozenset((triple[i], triple[j])) in used_pairs
                       for i in range(3) for j in range(i + 1, 3))

    while len(sets) < TOTAL_STARTER_SETS:
        pick = min((t for t in cycles if free(t)),
                   key=lambda t: (sum(uses.get(s, 0) for s in t), t), default=None)
        if pick is None:
            break
        take(pick, "cycle")

    print_report(f"starter sets: {len(sets)} of {TOTAL_STARTER_SETS} "
                 f"({len(STARTER_SET_PIECES)} hand written), "
                 f"{len(cycles)} cycles available, "
                 f"{len(uses)} of {len(candidates)} species used")
    if len(sets) < TOTAL_STARTER_SETS:
        raise SystemExit("ran out of cycles that share no pair with an existing set")
    return sets


def base_stat_total(path):
    """Sum the five stats. They are the first bare 5-value db in the record."""
    for line in path.read_text().splitlines():
        line = line.split(";")[0].strip()
        m = re.match(r"^db\s+(\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*\d+)\s*$", line)
        if m:
            return sum(int(v) for v in m.group(1).split(","))
    raise ValueError(f"no stat line found in {path}")


def main():
    names = parse_species_constants()
    dex_order = parse_dex_order()
    stat_files = parse_base_stat_files()
    names_to_index = {n: i for i, n in names.items()}
    NUM_INDEXES = max(dex_order)

    # dex constant name -> dex number, taken from the include order
    dex_name_of_number = {}
    for dex, path in stat_files.items():
        for line in path.read_text().splitlines():
            m = re.match(r"^\s*db\s+(DEX_[A-Z0-9_]+)", line)
            if m:
                dex_name_of_number[m.group(1)] = dex
                break

    # Group internal indices by the dex entry they claim.
    claims = {}
    for index, dex_name in dex_order.items():
        claims.setdefault(dex_name, []).append(index)

    pool = []
    dropped = []
    for dex_name, indices in sorted(claims.items()):
        if dex_name == "DEX_MISSINGNO":
            dropped += [(i, names.get(i, "?"), "missingno") for i in indices]
            continue
        dex_num = dex_name_of_number.get(dex_name)
        if dex_num is None:
            dropped += [(i, names.get(i, "?"), "no base stats") for i in indices]
            continue
        # Canonical index: constant name matches the dex constant name.
        canonical = [i for i in indices if names.get(i) == dex_name[len("DEX_"):]]
        if len(canonical) != 1:
            raise SystemExit(
                f"cannot resolve canonical index for {dex_name}: "
                f"{[(i, names.get(i)) for i in indices]}"
            )
        keep = canonical[0]
        if dex_num in LEGENDARY_DEX:
            dropped.append((keep, names[keep], "legendary"))
            dropped += [
                (i, names.get(i, "?"), f"variant of {dex_name}")
                for i in indices if i != keep
            ]
            continue
        pool.append((keep, names[keep], base_stat_total(stat_files[dex_num])))
        dropped += [
            (i, names.get(i, "?"), f"variant of {dex_name}")
            for i in indices if i != keep
        ]

    pool.sort(key=lambda e: e[2])

    print(f"pool: {len(pool)} species, dropped: {len(dropped)}")
    print(f"BST range: {pool[0][2]} ({pool[0][1]}) .. {pool[-1][2]} ({pool[-1][1]})")
    for index, name, why in sorted(dropped):
        print(f"  dropped ${index:02X} {name:<20} {why}")

    n = len(pool)
    bsts = [bst for _, _, bst in pool]

    # Widest run of indexes each position may exchange with. Sorted order makes
    # these contiguous, and comparing against the smaller total keeps the
    # relation mutual: j is in i's window exactly when i is in j's.
    window_lo, window_hi = [], []
    for i, bst in enumerate(bsts):
        lo = i
        while lo > 0 and bst - bsts[lo - 1] <= BST_TOLERANCE * bsts[lo - 1]:
            lo -= 1
        hi = i
        while hi < n - 1 and bsts[hi + 1] - bst <= BST_TOLERANCE * bst:
            hi += 1
        window_lo.append(lo)
        window_hi.append(hi)

    for i in range(n):
        while window_hi[i] - window_lo[i] + 1 < MIN_WINDOW:
            below, above = window_lo[i] > 0, window_hi[i] < n - 1
            if not below and not above:
                break
            if below and (not above or i - window_lo[i] <= window_hi[i] - i):
                window_lo[i] -= 1
            else:
                window_hi[i] += 1

    # Widening one side has to be mirrored on the other. TryWindowSwap tests both
    # occupants, so a pairing only one of them allows is rejected, and the pool
    # would mix worse rather than better.
    changed = True
    while changed:
        changed = False
        for i in range(n):
            for j in range(window_lo[i], window_hi[i] + 1):
                if i < window_lo[j]:
                    window_lo[j], changed = i, True
                elif i > window_hi[j]:
                    window_hi[j], changed = i, True
    for i in range(n):
        for j in range(window_lo[i], window_hi[i] + 1):
            assert window_lo[j] <= i <= window_hi[j], \
                f"{pool[i][1]} may take {pool[j][1]} but not the reverse"

    widths = [window_hi[i] - window_lo[i] + 1 for i in range(n)]
    print(f"window sizes: min {min(widths)}, max {max(widths)}, "
          f"mean {sum(widths) / n:.1f}")

    # A replacement may only stand in where it could legitimately exist: the
    # earliest level it can be held at must be at or under the lowest level the
    # species it replaces is ever met at. Capping at the floor keeps identity
    # legal for every slot, so a valid permutation always exists.
    flat_to_name = {flatten(name): name for name in names_to_index}
    min_levels = compute_min_levels(
        parse_evolution_levels(flat_to_name), names_to_index)
    wild_low = parse_encounter_levels()
    min_level_table, level_cap_table = [], []
    for _, name, _ in pool:
        floor = min_levels.get(name, 1)
        min_level_table.append(floor)
        # Flooring the cap at the species' own minimum keeps identity legal for
        # every slot, so the shuffle can never be handed one nothing can fill.
        level_cap_table.append(max(floor, wild_low.get(name, floor)))

    legal = [sum(1 for i in range(n)
                 if min_level_table[i] <= level_cap_table[j]
                 and window_lo[j] <= i <= window_hi[j])
             for j in range(n)]
    assert min(legal) > 1, \
        f"nothing but itself can fill {pool[legal.index(min(legal))][1]}'s slot"
    print(f"level floors: {min(min_level_table)}..{max(min_level_table)}, "
          f"caps: {min(level_cap_table)}..{max(level_cap_table)}, "
          f"legal partners: {min(legal)}..{max(legal)} (median "
          f"{sorted(legal)[n // 2]})")

    item_pool = build_item_pool()
    leaked = [i for i in item_pool
              if i in parse_key_items() or re.match(r"^(TM|HM)\d", i)]
    assert not leaked, f"key items, TMs or HMs leaked into the item pool: {leaked}"
    print(f"item pool: {len(item_pool)} items")

    OUT_CONSTANTS.write_text("\n".join([
        "; Generated by tools/generate_species_pool.py -- do not edit by hand.",
        "; Sizes of the randomizer species pool in data/randomizer/species_pool.asm.",
        "; Separate from the data so wram.asm can size its buffers.",
        "",
        f"DEF RANDO_POOL_SIZE EQU {n}",
        f"DEF RANDO_ITEM_POOL_SIZE EQU {len(item_pool)}",
        "",
    ]))

    lines = [
        "; Generated by tools/generate_species_pool.py -- do not edit by hand.",
        "; Valid randomizer species sorted by base-stat total, with the range of",
        "; positions each may be exchanged with.",
        "",
        "RandoPool::",
        "\ttable_width 1",
    ]
    lines += [f"\tdb {name} ; {bst}" for _, name, bst in pool]
    lines += [f"\tassert_table_length RANDO_POOL_SIZE", ""]

    for label, table in (("RandoWindowLo", window_lo), ("RandoWindowHi", window_hi)):
        lines += [f"{label}::", "\ttable_width 1"]
        lines += [f"\tdb {v}" for v in table]
        lines += [f"\tassert_table_length RANDO_POOL_SIZE", ""]

    # A wild swap is only legal when the incoming mon's floor is at or under the
    # slot's cap, which is what keeps a level 3 Kakuna out of Route 1. Opposing
    # teams are exempt: their mons are fought, never held, so no level there has
    # to be one the player could have reached.
    for label, table in (("RandoMinLevel", min_level_table),
                         ("RandoLevelCap", level_cap_table)):
        lines += [f"{label}::", "\ttable_width 1"]
        lines += [f"\tdb {v}" for v in table]
        lines += ["\tassert_table_length RANDO_POOL_SIZE", ""]

    lines += [
        "; What a randomized ground or hidden item may turn into. Key items, HMs",
        "; and TMs are absent: several key items are plain item balls, and moving",
        "; those can leave a seed unwinnable.",
        "RandoItemPool::",
        "\ttable_width 1",
    ]
    lines += [f"\tdb {item}" for item in item_pool]
    lines += ["\tassert_table_length RANDO_ITEM_POOL_SIZE", ""]

    # species index -> position in RandoPool, so a shuffled entry can be checked
    # against the window of wherever it has landed
    pos_of = {idx: i for i, (idx, _, _) in enumerate(pool)}
    lines += [
        "; position in RandoPool for each species index, 255 if not in the pool",
        "RandoPoolPos::",
        "\ttable_width 1",
    ]
    lines += [f"\tdb {pos_of.get(i, 255)}" for i in range(NUM_INDEXES + 1)]
    lines += [f"\tassert_table_length {NUM_INDEXES + 1}", ""]

    # HM learner flags, indexed by internal species index so the guardrail can
    # look straight up whatever a shuffle slot now holds.
    learners = parse_hm_learners(stat_files, names_to_index)
    lines += [
        "; bit 0 = " + ", bit 1 = ".join(HM_MOVES[:2]) + f", bit 2 = {HM_MOVES[2]}",
        "; indexed by internal species index",
        "RandoHmLearners::",
        "\ttable_width 1",
    ]
    for i in range(0, NUM_INDEXES + 1):
        lines.append(f"\tdb {learners.get(i, 0)}")
    lines += [f"\tassert_table_length {NUM_INDEXES + 1}", ""]

    early = parse_early_species(names_to_index)
    anchors = [i for i, (idx, _, _) in enumerate(pool) if idx in early]
    lines += [
        "; pool positions of species catchable before Surf is needed. If none of",
        "; their replacements can learn a field move, the guardrail swaps one in.",
        f"DEF RANDO_NUM_ANCHORS EQU {len(anchors)}",
        "RandoAnchors::",
        "\ttable_width 1",
    ]
    lines += [f"\tdb {a}" for a in anchors]
    lines += [f"\tassert_table_length RANDO_NUM_ANCHORS", ""]
    print(f"early anchors: {len(anchors)} pool positions")
    for i, move in enumerate(HM_MOVES):
        n = sum(1 for idx, _, _ in pool if learners.get(idx, 0) & (1 << i))
        print(f"  {move}: {n} of {len(pool)} pool species can learn it")

    # what each species evolves into, for carrying the rival's starter forward
    evolves = parse_evolutions()
    lines += [
        "; what each species evolves into, 0 for none, by internal species index",
        "RandoEvolvesTo::",
        "\ttable_width 1",
    ]
    by_flat = {flatten(n): n for n in names.values()}
    for i in range(NUM_INDEXES + 1):
        target = evolves.get(flatten(names[i]), None) if i in names else None
        lines.append(f"\tdb {by_flat.get(flatten(target), 0) if target else 0}")
    lines += [f"\tassert_table_length {NUM_INDEXES + 1}", ""]

    types = parse_species_types(stat_files)
    legendaries = {n[len("DEX_"):] for n, d in dex_name_of_number.items()
                   if d in LEGENDARY_DEX}
    starter_sets = build_starter_sets(types, evolves, legendaries, print)
    lines += [
        "; Starter sets. The player takes one and the rival the next along, so the",
        "; order carries the matchup. All but the first few are type cycles: each",
        "; beats the next and loses to the one before.",
        "RandoStarterTriples::",
        "\ttable_width 3",
    ]
    for label, members in starter_sets:
        lines.append(f"\tdb {', '.join(members)} ; {label}")
    lines += ["\tassert_table_length RANDO_NUM_STARTER_SETS", ""]

    OUT_CONSTANTS.write_text(OUT_CONSTANTS.read_text().rstrip("\n") + "\n" +
                             f"DEF RANDO_NUM_STARTER_SETS EQU {len(starter_sets)}\n")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT.relative_to(ROOT)} and {OUT_CONSTANTS.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
