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

The surviving pool is sorted by base-stat total and split into equal-sized
buckets. At runtime the randomizer shuffles within each bucket, which keeps
replacements roughly power-matched so early routes stay survivable.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Replacements must be within this fraction of the original's base stat total,
# measured against the smaller of the two so the relation is symmetric.
BST_TOLERANCE = 0.10

# Dex numbers the Universal Pokemon Randomizer treats as legendary. Kept out of
# the pool entirely, so they are never handed out and never replaced.
LEGENDARY_DEX = {144, 145, 146, 150, 151}

CONSTANTS = ROOT / "constants/pokemon_constants.asm"
DEX_ORDER = ROOT / "data/pokemon/dex_order.asm"
BASE_STATS = ROOT / "data/pokemon/base_stats.asm"
OUT = ROOT / "data/randomizer/species_pool.asm"
OUT_CONSTANTS = ROOT / "constants/randomizer_constants.asm"


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

    widths = [window_hi[i] - window_lo[i] + 1 for i in range(n)]
    print(f"window sizes: min {min(widths)}, max {max(widths)}, "
          f"mean {sum(widths) / n:.1f}")

    OUT_CONSTANTS.write_text("\n".join([
        "; Generated by tools/generate_species_pool.py -- do not edit by hand.",
        "; Sizes of the randomizer species pool in data/randomizer/species_pool.asm.",
        "; Separate from the data so wram.asm can size its buffers.",
        "",
        f"DEF RANDO_POOL_SIZE EQU {n}",
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

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT.relative_to(ROOT)} and {OUT_CONSTANTS.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
