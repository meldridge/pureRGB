#!/usr/bin/env python3
"""Run the randomizer's permutation generator out of a built ROM and check it.

There is no emulator available here, so this interprets the LR35902 subset the
randomizer routines actually use, executing the assembled bytes straight from
pokered.gbc. That means it tests the shipped code rather than a Python
restatement of the algorithm -- register handling bugs in the shuffle show up.

Usage: tools/test_randomizer.py [rom.gbc]
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
Z, N, H, C = 0x80, 0x40, 0x20, 0x10


class Cpu:
    """Enough of a Game Boy CPU to run self-contained computational routines."""

    def __init__(self, rom, bank):
        self.rom = rom
        self.bank = bank
        self.ram = bytearray(0x10000)  # only $8000+ is meaningful
        self.a = self.b = self.c = self.d = self.e = self.h = self.l = 0
        self.f = 0
        self.sp = 0xDFFF
        self.pc = 0
        self.steps = 0

    # memory -------------------------------------------------------------
    def rd(self, addr):
        addr &= 0xFFFF
        if addr < 0x4000:
            return self.rom[addr]
        if addr < 0x8000:
            return self.rom[self.bank * 0x4000 + (addr - 0x4000)]
        return self.ram[addr]

    def wr(self, addr, val):
        addr &= 0xFFFF
        if 0x2000 <= addr < 0x4000:
            # rom bank select, so calls that switch banks land in the right code
            self.bank = (val & 0x7F) or 1
            return
        if addr < 0x8000:
            return  # other MBC registers (rRAMG/rBMODE/rRAMB) -- no effect here
        self.ram[addr] = val & 0xFF

    def rd16(self, addr):
        return self.rd(addr) | (self.rd(addr + 1) << 8)

    # register pairs -----------------------------------------------------
    def get_bc(self): return (self.b << 8) | self.c
    def get_de(self): return (self.d << 8) | self.e
    def get_hl(self): return (self.h << 8) | self.l
    def set_bc(self, v): self.b, self.c = (v >> 8) & 0xFF, v & 0xFF
    def set_de(self, v): self.d, self.e = (v >> 8) & 0xFF, v & 0xFF
    def set_hl(self, v): self.h, self.l = (v >> 8) & 0xFF, v & 0xFF

    def flag(self, mask): return bool(self.f & mask)

    def setf(self, z=None, n=None, h=None, c=None):
        for mask, val in ((Z, z), (N, n), (H, h), (C, c)):
            if val is None:
                continue
            self.f = (self.f | mask) if val else (self.f & ~mask & 0xFF)

    # alu ----------------------------------------------------------------
    def alu(self, op, val):
        a = self.a
        if op == 0:    # add
            r = a + val
            self.setf(r & 0xFF == 0, False, (a & 0xF) + (val & 0xF) > 0xF, r > 0xFF)
            self.a = r & 0xFF
        elif op == 1:  # adc
            cy = 1 if self.flag(C) else 0
            r = a + val + cy
            self.setf(r & 0xFF == 0, False, (a & 0xF) + (val & 0xF) + cy > 0xF, r > 0xFF)
            self.a = r & 0xFF
        elif op == 2:  # sub
            r = a - val
            self.setf(r & 0xFF == 0, True, (a & 0xF) < (val & 0xF), r < 0)
            self.a = r & 0xFF
        elif op == 3:  # sbc
            cy = 1 if self.flag(C) else 0
            r = a - val - cy
            self.setf(r & 0xFF == 0, True, (a & 0xF) < (val & 0xF) + cy, r < 0)
            self.a = r & 0xFF
        elif op == 4:  # and
            self.a = a & val
            self.setf(self.a == 0, False, True, False)
        elif op == 5:  # xor
            self.a = a ^ val
            self.setf(self.a == 0, False, False, False)
        elif op == 6:  # or
            self.a = a | val
            self.setf(self.a == 0, False, False, False)
        elif op == 7:  # cp
            r = a - val
            self.setf(r & 0xFF == 0, True, (a & 0xF) < (val & 0xF), r < 0)

    def inc8(self, v):
        r = (v + 1) & 0xFF
        self.setf(r == 0, False, (v & 0xF) == 0xF)
        return r

    def dec8(self, v):
        r = (v - 1) & 0xFF
        self.setf(r == 0, True, (v & 0xF) == 0)
        return r

    # register file access by opcode index -------------------------------
    def get_r(self, i):
        return [self.b, self.c, self.d, self.e, self.h, self.l,
                self.rd(self.get_hl()), self.a][i]

    def set_r(self, i, v):
        v &= 0xFF
        if i == 0: self.b = v
        elif i == 1: self.c = v
        elif i == 2: self.d = v
        elif i == 3: self.e = v
        elif i == 4: self.h = v
        elif i == 5: self.l = v
        elif i == 6: self.wr(self.get_hl(), v)
        else: self.a = v

    def cond(self, i):
        return [not self.flag(Z), self.flag(Z), not self.flag(C), self.flag(C)][i]

    def push(self, v):
        self.sp = (self.sp - 2) & 0xFFFF
        self.wr(self.sp, v & 0xFF)
        self.wr(self.sp + 1, (v >> 8) & 0xFF)

    def pop(self):
        v = self.rd16(self.sp)
        self.sp = (self.sp + 2) & 0xFFFF
        return v

    # execution ----------------------------------------------------------
    def run(self, entry, limit=80_000_000):
        SENTINEL = 0xF000
        self.pc = entry
        self.push(SENTINEL)
        while self.pc != SENTINEL:
            self.step()
            self.steps += 1
            if self.steps > limit:
                raise RuntimeError("execution did not terminate")

    def step(self):
        op = self.rd(self.pc)
        self.pc = (self.pc + 1) & 0xFFFF

        def imm8():
            v = self.rd(self.pc)
            self.pc = (self.pc + 1) & 0xFFFF
            return v

        def imm16():
            v = self.rd16(self.pc)
            self.pc = (self.pc + 2) & 0xFFFF
            return v

        if op == 0x00:  # nop
            return
        if op == 0xCB:
            self.step_cb(imm8())
            return

        # ld r, r'  /  halt
        if 0x40 <= op <= 0x7F:
            dst, src = (op >> 3) & 7, op & 7
            if op == 0x76:
                raise RuntimeError("halt")
            self.set_r(dst, self.get_r(src))
            return

        # alu a, r
        if 0x80 <= op <= 0xBF:
            self.alu((op >> 3) & 7, self.get_r(op & 7))
            return

        # alu a, n
        if op in (0xC6, 0xCE, 0xD6, 0xDE, 0xE6, 0xEE, 0xF6, 0xFE):
            self.alu((op >> 3) & 7, imm8())
            return

        # ld r, n
        if op & 0xC7 == 0x06:
            self.set_r((op >> 3) & 7, imm8())
            return

        # inc r / dec r
        if op & 0xC7 == 0x04:
            i = (op >> 3) & 7
            self.set_r(i, self.inc8(self.get_r(i)))
            return
        if op & 0xC7 == 0x05:
            i = (op >> 3) & 7
            self.set_r(i, self.dec8(self.get_r(i)))
            return

        # 16 bit loads / inc / dec / add hl
        pairs = {0: self.set_bc, 1: self.set_de, 2: self.set_hl}
        getters = {0: self.get_bc, 1: self.get_de, 2: self.get_hl}
        if op & 0xCF == 0x01:
            i = (op >> 4) & 3
            v = imm16()
            (pairs[i] if i in pairs else None)(v) if i in pairs else setattr(self, "sp", v)
            return
        if op & 0xCF == 0x03:  # inc rr
            i = (op >> 4) & 3
            if i in pairs: pairs[i]((getters[i]() + 1) & 0xFFFF)
            else: self.sp = (self.sp + 1) & 0xFFFF
            return
        if op & 0xCF == 0x0B:  # dec rr
            i = (op >> 4) & 3
            if i in pairs: pairs[i]((getters[i]() - 1) & 0xFFFF)
            else: self.sp = (self.sp - 1) & 0xFFFF
            return
        if op & 0xCF == 0x09:  # add hl, rr
            i = (op >> 4) & 3
            v = getters[i]() if i in pairs else self.sp
            hl = self.get_hl()
            r = hl + v
            self.setf(None, False, (hl & 0xFFF) + (v & 0xFFF) > 0xFFF, r > 0xFFFF)
            self.set_hl(r & 0xFFFF)
            return

        # indirect loads
        if op == 0x02: self.wr(self.get_bc(), self.a); return
        if op == 0x12: self.wr(self.get_de(), self.a); return
        if op == 0x0A: self.a = self.rd(self.get_bc()); return
        if op == 0x1A: self.a = self.rd(self.get_de()); return
        if op == 0x22:  # ld [hli], a
            self.wr(self.get_hl(), self.a); self.set_hl((self.get_hl() + 1) & 0xFFFF); return
        if op == 0x32:
            self.wr(self.get_hl(), self.a); self.set_hl((self.get_hl() - 1) & 0xFFFF); return
        if op == 0x2A:  # ld a, [hli]
            self.a = self.rd(self.get_hl()); self.set_hl((self.get_hl() + 1) & 0xFFFF); return
        if op == 0x3A:
            self.a = self.rd(self.get_hl()); self.set_hl((self.get_hl() - 1) & 0xFFFF); return
        if op == 0xEA: self.wr(imm16(), self.a); return
        if op == 0xFA: self.a = self.rd(imm16()); return
        if op == 0xE0: self.wr(0xFF00 + imm8(), self.a); return
        if op == 0xF0: self.a = self.rd(0xFF00 + imm8()); return
        if op == 0xE2: self.wr(0xFF00 + self.c, self.a); return
        if op == 0xF2: self.a = self.rd(0xFF00 + self.c); return

        # jumps
        if op == 0x18:
            off = imm8()
            self.pc = (self.pc + (off - 256 if off > 127 else off)) & 0xFFFF
            return
        if op & 0xE7 == 0x20:  # jr cc
            off = imm8()
            if self.cond((op >> 3) & 3):
                self.pc = (self.pc + (off - 256 if off > 127 else off)) & 0xFFFF
            return
        if op == 0xC3: self.pc = imm16(); return
        if op & 0xE7 == 0xC2:
            t = imm16()
            if self.cond((op >> 3) & 3): self.pc = t
            return
        if op == 0xE9: self.pc = self.get_hl(); return

        # calls / returns
        if op == 0xCD:
            t = imm16(); self.push(self.pc); self.pc = t; return
        if op & 0xE7 == 0xC4:
            t = imm16()
            if self.cond((op >> 3) & 3):
                self.push(self.pc); self.pc = t
            return
        if op == 0xC9: self.pc = self.pop(); return
        if op & 0xE7 == 0xC0:
            if self.cond((op >> 3) & 3): self.pc = self.pop()
            return
        if op & 0xC7 == 0xC7:  # rst
            self.push(self.pc); self.pc = op & 0x38; return

        # stack
        if op & 0xCF == 0xC5:
            i = (op >> 4) & 3
            v = [self.get_bc(), self.get_de(), self.get_hl(),
                 (self.a << 8) | self.f][i]
            self.push(v); return
        if op & 0xCF == 0xC1:
            i = (op >> 4) & 3
            v = self.pop()
            if i == 3:
                self.a, self.f = (v >> 8) & 0xFF, v & 0xF0
            else:
                [self.set_bc, self.set_de, self.set_hl][i](v)
            return

        # rotates on a
        if op == 0x07:  # rlca
            c = self.a >> 7
            self.a = ((self.a << 1) | c) & 0xFF
            self.setf(False, False, False, bool(c)); return
        if op == 0x0F:  # rrca
            c = self.a & 1
            self.a = (self.a >> 1) | (c << 7)
            self.setf(False, False, False, bool(c)); return
        if op == 0x17:  # rla
            c = 1 if self.flag(C) else 0
            nc = self.a >> 7
            self.a = ((self.a << 1) | c) & 0xFF
            self.setf(False, False, False, bool(nc)); return
        if op == 0x1F:  # rra
            c = 1 if self.flag(C) else 0
            nc = self.a & 1
            self.a = (self.a >> 1) | (c << 7)
            self.setf(False, False, False, bool(nc)); return

        if op == 0x37: self.setf(None, False, False, True); return   # scf
        if op == 0x3F: self.setf(None, False, False, not self.flag(C)); return  # ccf
        if op == 0x2F:
            self.a ^= 0xFF; self.setf(None, True, True, None); return  # cpl
        if op in (0xF3, 0xFB): return  # di / ei

        raise RuntimeError(f"unimplemented opcode ${op:02X} at ${self.pc - 1:04X}")

    def step_cb(self, op):
        i = op & 7
        v = self.get_r(i)
        kind = op >> 6
        if kind == 0:
            sub = (op >> 3) & 7
            if sub == 0:    # rlc
                c = v >> 7; r = ((v << 1) | c) & 0xFF
            elif sub == 1:  # rrc
                c = v & 1; r = (v >> 1) | (c << 7)
            elif sub == 2:  # rl
                c = v >> 7; r = ((v << 1) | (1 if self.flag(C) else 0)) & 0xFF
            elif sub == 3:  # rr
                c = v & 1; r = (v >> 1) | ((1 if self.flag(C) else 0) << 7)
            elif sub == 4:  # sla
                c = v >> 7; r = (v << 1) & 0xFF
            elif sub == 5:  # sra
                c = v & 1; r = (v >> 1) | (v & 0x80)
            elif sub == 6:  # swap
                c = 0; r = ((v << 4) | (v >> 4)) & 0xFF
            else:           # srl
                c = v & 1; r = v >> 1
            self.set_r(i, r)
            self.setf(r == 0, False, False, bool(c))
            return
        bit = (op >> 3) & 7
        if kind == 1:
            self.setf(not (v >> bit) & 1, False, True)
        elif kind == 2:
            self.set_r(i, v & ~(1 << bit))
        else:
            self.set_r(i, v | (1 << bit))


def load_symbols(path):
    syms = {}
    for line in Path(path).read_text().splitlines():
        m = re.match(r"^([0-9a-fA-F]{2}):([0-9a-fA-F]{4})\s+(\S+)$", line.strip())
        if m:
            syms.setdefault(m.group(3), (int(m.group(1), 16), int(m.group(2), 16)))
    return syms


def parse_pool():
    """(pool order, bucket bounds) straight from the generated table."""
    text = (ROOT / "data/randomizer/species_pool.asm").read_text()
    consts = (ROOT / "constants/pokemon_constants.asm").read_text()
    index, names = None, {}
    for line in consts.splitlines():
        line = line.split(";")[0].strip()
        if line.startswith("const_def"):
            index = 0
            continue
        if index is None:
            continue
        m = re.match(r"^const\s+([A-Z0-9_]+)$", line)
        if m:
            names[m.group(1)] = index; index += 1; continue
        m = re.match(r"^const_skip\s*(\d*)$", line)
        if m:
            index += int(m.group(1) or 1)
    sections, current = {}, None
    for line in text.splitlines():
        s = line.split(";")[0].strip()
        m = re.match(r"^(Rando\w+)::", s)
        if m:
            current = m.group(1)
            sections[current] = []
            continue
        m = re.match(r"^db\s+(\S+)$", s)
        if m and current:
            v = m.group(1)
            sections[current].append(names[v] if v in names else int(v))
    return (sections["RandoPool"], sections["RandoBucketBounds"],
            sections["RandoHmLearners"], sections["RandoAnchors"])


def generate(rom, syms, seed):
    bank, _ = syms["GenerateSpeciesMap"]
    cpu = Cpu(rom, bank)
    _, magic = syms["sRandoMagic"]
    _, sseed = syms["sRandoSeed"]
    for i, ch in enumerate(b"RAND"):
        cpu.ram[magic + i] = ch
    for i in range(4):
        cpu.ram[sseed + i] = (seed >> (8 * i)) & 0xFF
    cpu.run(syms["EnsureSpeciesMap"][1])
    _, mapaddr = syms["sRandoMap"]
    return bytes(cpu.ram[mapaddr:mapaddr + 191]), cpu.steps


def wild_data_test(rom, syms, seed, species_map, check):
    """Drive RandoRemapWildData over a faked pair of wild data ram copies."""
    bank, _ = syms["RandoRemapWildData"]
    cpu = Cpu(rom, bank)
    for i, ch in enumerate(b"RAND"):
        cpu.ram[syms["sRandoMagic"][1] + i] = ch
    for i in range(4):
        cpu.ram[syms["sRandoSeed"][1] + i] = (seed >> (8 * i)) & 0xFF

    grass_rate, grass_mons = syms["wGrassRate"][1], syms["wGrassMons"][1]
    water_rate, water_mons = syms["wWaterRate"][1], syms["wWaterMons"][1]

    # grass is populated, water is not -- a rate of 0 means the buffer is stale
    cpu.ram[grass_rate] = 25
    cpu.ram[water_rate] = 0
    grass_in = []
    for slot in range(10):
        level, species = 3 + slot, 1 + slot * 7
        cpu.ram[grass_mons + slot * 2] = level
        cpu.ram[grass_mons + slot * 2 + 1] = species
        grass_in.append((level, species))
    water_sentinel = bytes(range(0x40, 0x54))
    cpu.ram[water_mons:water_mons + 20] = water_sentinel

    cpu.run(syms["RandoRemapWildData"][1])

    levels_ok = all(cpu.ram[grass_mons + s * 2] == grass_in[s][0] for s in range(10))
    species_ok = all(
        cpu.ram[grass_mons + s * 2 + 1] == species_map[grass_in[s][1]]
        for s in range(10)
    )
    check("wild: levels untouched", levels_ok)
    check("wild: species remapped through the table", species_ok)
    check("wild: rate 0 leaves stale buffer alone",
          bytes(cpu.ram[water_mons:water_mons + 20]) == water_sentinel)

    # and with the randomizer off, nothing at all should move
    cpu2 = Cpu(rom, bank)
    cpu2.ram[grass_rate] = 25
    for slot in range(10):
        cpu2.ram[grass_mons + slot * 2] = 3 + slot
        cpu2.ram[grass_mons + slot * 2 + 1] = 1 + slot * 7
    before = bytes(cpu2.ram[grass_mons:grass_mons + 20])
    cpu2.run(syms["RandoRemapWildData"][1])
    check("wild: untouched when randomizer is off",
          bytes(cpu2.ram[grass_mons:grass_mons + 20]) == before)


def apply_species_test(rom, syms, seed, species_map, pool, check):
    """ApplyRandoSpecies is the single-species entry point the rod hook uses."""
    bank, entry = syms["ApplyRandoSpecies"]

    def call(species, enabled):
        cpu = Cpu(rom, bank)
        if enabled:
            for i, ch in enumerate(b"RAND"):
                cpu.ram[syms["sRandoMagic"][1] + i] = ch
            for i in range(4):
                cpu.ram[syms["sRandoSeed"][1] + i] = (seed >> (8 * i)) & 0xFF
        cpu.e = species
        cpu.b = cpu.c = cpu.d = cpu.h = cpu.l = 0x5A  # canaries
        cpu.run(entry)
        return cpu.e, (cpu.b, cpu.c, cpu.h, cpu.l)

    sample = pool[:5] + pool[-5:]
    check("apply: maps through the table",
          all(call(s, True)[0] == species_map[s] for s in sample))
    check("apply: identity when randomizer off",
          all(call(s, False)[0] == s for s in sample))
    check("apply: NO_MON passes through", call(0, True)[0] == 0)
    check("apply: out of range index passes through", call(0xBF, True)[0] == 0xBF)
    check("apply: preserves bc and hl",
          all(call(s, True)[1] == (0x5A, 0x5A, 0x5A, 0x5A) for s in sample))

    # the trainer party hook goes through wram instead of a register
    pbank, pentry = syms["RandoRemapPartySpecies"]
    cur = syms["wCurPartySpecies"][1]

    def call_party(species, enabled):
        cpu = Cpu(rom, pbank)
        if enabled:
            for i, ch in enumerate(b"RAND"):
                cpu.ram[syms["sRandoMagic"][1] + i] = ch
            for i in range(4):
                cpu.ram[syms["sRandoSeed"][1] + i] = (seed >> (8 * i)) & 0xFF
        cpu.ram[cur] = species
        cpu.run(pentry)
        return cpu.ram[cur]

    check("party: maps wCurPartySpecies",
          all(call_party(s, True) == species_map[s] for s in sample))
    check("party: identity when randomizer off",
          all(call_party(s, False) == s for s in sample))


def new_game_test(rom, syms, check):
    """The new game gate must arm or disarm the randomizer every time.

    RandoNewGame itself shows the seed entry screen, which needs a real console,
    so the seed derivation is tested through RandoSeedFromBuffer -- the half that
    runs once the player has typed something.
    """
    magic, sseed = syms["sRandoMagic"][1], syms["sRandoSeed"][1]

    def seed_from(text):
        bank, entry = syms["RandoSeedFromBuffer"]
        cpu = Cpu(rom, bank)
        buf = syms["wStringBuffer"][1]
        encoded = bytes((0x80 + (ord(c) - ord("A"))) for c in text) + b"\x50"
        cpu.ram[buf:buf + len(encoded)] = encoded
        cpu.ram[syms["hRandomAdd"][1]] = 0x3C
        cpu.ram[syms["hRandomSub"][1]] = 0x91
        cpu.ram[syms["hLoadedROMBank"][1]] = bank
        cpu.run(entry)
        return (bytes(cpu.ram[magic:magic + 4]), bytes(cpu.ram[sseed:sseed + 4]))

    m, s = seed_from("JOLTEON")
    check("seed: typed seed arms the randomizer", m == b"RAND")
    check("seed: typed seed is nonzero", any(s))
    check("seed: same text gives the same seed", seed_from("JOLTEON")[1] == s)
    check("seed: different text gives a different seed",
          seed_from("JOLTEOO")[1] != s)
    check("seed: blank rolls a seed anyway", any(seed_from("")[1]))

    # the off path returns before the entry screen, so it can be run whole
    bank, entry = syms["RandoNewGame"]
    cpu = Cpu(rom, bank)
    cpu.ram[syms["wOptions3"][1]] = 0  # BIT_RANDOMIZER clear
    cpu.ram[magic:magic + 4] = b"RAND"  # stale state from a previous game
    cpu.ram[sseed:sseed + 4] = bytes([9, 9, 9, 9])
    cpu.ram[syms["hLoadedROMBank"][1]] = bank
    cpu.run(entry)
    check("new game: option off clears stale magic",
          bytes(cpu.ram[magic:magic + 4]) != b"RAND")
    check("new game: option off clears stale seed",
          not any(cpu.ram[sseed:sseed + 4]))


def main():
    rom_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "pokered.gbc"
    sym_path = rom_path.with_suffix(".sym")
    rom = rom_path.read_bytes()
    syms = load_symbols(sym_path)
    pool, bounds, hm_learners, anchors = parse_pool()
    poolset = set(pool)
    bucket_of = {}
    for b in range(len(bounds) - 1):
        for p in pool[bounds[b]:bounds[b + 1]]:
            bucket_of[p] = b

    failures = []

    def check(name, ok, detail=""):
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' -- ' + detail) if detail and not ok else ''}")
        if not ok:
            failures.append(name)

    print(f"rom: {rom_path.name}   pool: {len(pool)} species, {len(bounds) - 1} buckets\n")

    seeds = [0x00000001, 0x12345678, 0xDEADBEEF, 0x0000FFFF, 0xA5A5A5A5]
    maps = {}
    for seed in seeds:
        m, steps = generate(rom, syms, seed)
        maps[seed] = m
        print(f"seed ${seed:08X}  ({steps:,} instructions)")
        images = [m[s] for s in pool]
        check("bijection over pool", sorted(images) == sorted(pool),
              f"{len(set(images))} distinct of {len(pool)}")
        check("no value outside pool", all(v in poolset for v in images))
        check("bucket preserved", all(bucket_of[m[s]] == bucket_of[s] for s in pool))
        non_pool = [i for i in range(1, 191) if i not in poolset]
        check("non-pool species map to themselves", all(m[i] == i for i in non_pool))
        check("not the identity permutation", any(m[s] != s for s in pool),
              "every species mapped to itself")
        for bit, move in enumerate(("CUT", "SURF", "STRENGTH")):
            reachable = [hm_learners[m[pool[p]]] & (1 << bit) for p in anchors]
            check(f"a catchable mon can learn {move}", any(reachable))
        print()

    # wider sweep: the invariants must hold for every seed, not just five
    sweep_fail = []
    sweep = [(i * 0x9E3779B1) & 0xFFFFFFFF or 1 for i in range(1, 26)]
    for seed in sweep:
        m, _ = generate(rom, syms, seed)
        images = [m[s] for s in pool]
        if sorted(images) != sorted(pool):
            sweep_fail.append((seed, "not a bijection"))
        if any(bucket_of[m[s]] != bucket_of[s] for s in pool):
            sweep_fail.append((seed, "bucket escaped"))
        for bit, move in enumerate(("CUT", "SURF", "STRENGTH")):
            if not any(hm_learners[m[pool[p]]] & (1 << bit) for p in anchors):
                sweep_fail.append((seed, f"no {move} user"))
    check(f"sweep of {len(sweep)} seeds holds every invariant", not sweep_fail,
          "; ".join(f"${s:08X} {w}" for s, w in sweep_fail[:4]))

    m1, _ = generate(rom, syms, 0x12345678)
    check("deterministic (same seed twice)", m1 == maps[0x12345678])
    distinct = len({maps[s] for s in seeds})
    check("different seeds give different maps", distinct == len(seeds),
          f"only {distinct} distinct maps from {len(seeds)} seeds")

    print("\nwild encounter remap")
    wild_data_test(rom, syms, 0x12345678, maps[0x12345678], check)

    print("\nsingle species lookup (rod hook)")
    apply_species_test(rom, syms, 0x12345678, maps[0x12345678], pool, check)

    print("\nnew game gate")
    new_game_test(rom, syms, check)

    fixed = sum(1 for s in pool if maps[0x12345678][s] == s)
    print(f"\n  (seed $12345678 leaves {fixed}/{len(pool)} species unchanged)")

    print(f"\n{'ALL CHECKS PASSED' if not failures else 'FAILURES: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
