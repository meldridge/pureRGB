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


def parse_item_constants():
    """Item constant name -> item id."""
    ids, index = {}, None
    for line in (ROOT / "constants/item_constants.asm").read_text().splitlines():
        line = line.split(";")[0].strip()
        if line.startswith("const_def"):
            index = 0
            continue
        if index is None:
            continue
        m = re.match(r"^const\s+([A-Z0-9_]+)$", line)
        if m:
            ids[m.group(1)] = index
            index += 1
        elif re.match(r"^const_skip\s*(\d*)$", line):
            index += int(re.match(r"^const_skip\s*(\d*)$", line).group(1) or 1)
        elif re.match(r"^const_next\s+\$([0-9A-Fa-f]+)$", line):
            index = int(re.match(r"^const_next\s+\$([0-9A-Fa-f]+)$", line).group(1), 16)
    return ids


def parse_rando_flag_bits():
    """Bit position of each randomizer setting within the byte they share.

    They are declared consecutively from a byte boundary, which randomizer.asm
    asserts, so declaration order is bit order.
    """
    names = re.findall(r"^\tconst (FLAG_RANDOM_\w+)$",
                       (ROOT / "constants/event_constants.asm").read_text(), re.M)
    return {name: i for i, name in enumerate(names)}


RANDO_FLAG_BITS = parse_rando_flag_bits()


def parse_pool():
    """Pool order, windows and guardrail tables from the generated file."""
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
            if v in names:
                sections[current].append(names[v])
            elif v.isdigit():
                sections[current].append(int(v))
            else:
                sections[current].append(v)  # item constants, kept as names
    return (sections["RandoPool"], sections["RandoWindowLo"],
            sections["RandoWindowHi"], sections["RandoHmLearners"],
            sections["RandoAnchors"], sections["RandoItemPool"])


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

    # There is no table keyed by species; resolve one the way the game does, by
    # turning the species into a pool position and reading the shuffle.
    pbank, paddr = syms["RandoPoolPos"]
    ppos = pbank * 0x4000 + (paddr - 0x4000)

    def resolve(table):
        out = bytearray(191)
        for species in range(191):
            pos = rom[ppos + species]
            out[species] = species if pos == 0xFF else cpu.ram[table + pos]
        return bytes(out)

    starters = bytes(cpu.ram[syms["sRandoStarters"][1]:
                             syms["sRandoStarters"][1] + 3])
    return (resolve(syms["sRandoShuffle"][1]),
            resolve(syms["sRandoShuffleTrainer"][1]),
            starters, cpu.steps)


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


def apply_species_test(rom, syms, seed, species_map, trainer_map, pool, check):
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

    # the party hook goes through the opposing team table, not the wild one
    check("party: maps wCurPartySpecies",
          all(call_party(s, True) == trainer_map[s] for s in sample))
    check("party: identity when randomizer off",
          all(call_party(s, False) == s for s in sample))


def unmap_test(rom, syms, seed, species_map, pool, check):
    """RandoUnmapPokedexNum reads a gift back as the slot it came out of."""
    bank, entry = syms["RandoUnmapPokedexNum"]
    dex = syms["wPokedexNum"][1]

    def call(species, enabled):
        cpu = Cpu(rom, bank)
        if enabled:
            for i, ch in enumerate(b"RAND"):
                cpu.ram[syms["sRandoMagic"][1] + i] = ch
            for i in range(4):
                cpu.ram[syms["sRandoSeed"][1] + i] = (seed >> (8 * i)) & 0xFF
        cpu.ram[dex] = species
        cpu.run(entry)
        return cpu.ram[dex]

    check("unmap: inverts the map for every species in the pool",
          all(call(species_map[s], True) == s for s in pool))
    check("unmap: identity when randomizer off",
          all(call(species_map[s], False) == species_map[s] for s in pool[:5]))
    check("unmap: an index outside the pool stands for itself",
          call(0xBF, True) == 0xBF)

    # The Prize King checks these six against PrizeMonLevelDictionary, so each
    # has to survive the round trip or he rejects a prize he just sold.
    prizes = ("JYNX", "ELECTABUZZ", "TANGELA", "DRATINI", "DITTO", "PORYGON")
    sys.path.insert(0, str(ROOT / "tools"))
    from generate_species_pool import parse_species_constants
    name_of = parse_species_constants()
    index_of = {v: k for k, v in name_of.items()}
    check("unmap: every prize mon round-trips",
          all(call(species_map[index_of[p]], True) == index_of[p] for p in prizes))


def item_test(rom, syms, seed, item_pool, index_of_item, check):
    """Ground and hidden items roll per spot, from the seed and the location."""
    pool_ids = [index_of_item[name] for name in item_pool]
    potion = index_of_item["POTION"]

    def roll(entry, key_a, key_b, original, off_flag=None):
        bank, addr = syms[entry]
        cpu = Cpu(rom, bank)
        for i, ch in enumerate(b"RAND"):
            cpu.ram[syms["sRandoMagic"][1] + i] = ch
        for i in range(4):
            cpu.ram[syms["sRandoSeed"][1] + i] = (seed >> (8 * i)) & 0xFF
        if off_flag:
            cpu.ram[syms["sRandoFlags"][1]] = 1 << RANDO_FLAG_BITS[off_flag]
        if entry == "RandoRollGroundItem":
            cpu.ram[syms["wCurMap"][1]] = key_a
            cpu.ram[0xFF00 + (syms["hSpriteIndex"][1] & 0xFF)] = key_b
        else:
            cpu.ram[syms["wHiddenItemOrCoinsIndex"][1]] = key_a
        cpu.d = original
        cpu.run(addr)
        return cpu.e

    ground = [roll("RandoRollGroundItem", m, s, potion)
              for m in range(12) for s in range(1, 5)]
    hidden = [roll("RandoRollHiddenItem", i, 0, potion) for i in range(48)]

    check("items: every roll lands in the pool",
          all(v in pool_ids for v in ground + hidden))
    check("items: the same spot always rolls the same item",
          roll("RandoRollGroundItem", 5, 2, potion)
          == roll("RandoRollGroundItem", 5, 2, potion))
    check("items: different spots mostly differ",
          len(set(ground)) > len(pool_ids) // 2,
          f"only {len(set(ground))} distinct across {len(ground)} spots")
    # a ground spot and a hidden spot with the same index must not agree, or the
    # two key spaces have collapsed into one
    collisions = sum(1 for i in range(48)
                     if roll("RandoRollGroundItem", i, 0, potion) == hidden[i])
    check("items: ground and hidden keys do not collide",
          collisions < 12, f"{collisions} of 48 agreed")
    check("items: gate off leaves the item alone",
          roll("RandoRollGroundItem", 5, 2, potion, "FLAG_RANDOM_ITEMS_OFF") == potion)

    # Key items are absent from the pool, so a spot holding one is left alone by
    # the same test that keeps them from being handed out. This is what stops a
    # seed losing the Silph Scope.
    for key_item in ("SILPH_SCOPE", "LIFT_KEY", "SECRET_KEY", "GOLD_TEETH",
                     "CARD_KEY", "S_S_TICKET"):
        ident = index_of_item[key_item]
        if roll("RandoRollGroundItem", 5, 2, ident) != ident:
            check(f"items: {key_item} stays where it is", False)
            break
    else:
        check("items: key item spots are left alone", True)
    check("items: no key item can be handed out",
          not ({"SILPH_SCOPE", "LIFT_KEY", "SECRET_KEY", "GOLD_TEETH",
                "CARD_KEY", "S_S_TICKET", "HM_SURF"} & set(item_pool)))


def tm_test(rom, syms, seeds, check):
    """The tm order must be a permutation, and the gate must switch it off."""
    num_tms = 50
    tm01 = 0xC9

    def order(seed, off=False):
        bank, entry = syms["EnsureSpeciesMap"]
        cpu = Cpu(rom, bank)
        for i, ch in enumerate(b"RAND"):
            cpu.ram[syms["sRandoMagic"][1] + i] = ch
        for i in range(4):
            cpu.ram[syms["sRandoSeed"][1] + i] = (seed >> (8 * i)) & 0xFF
        cpu.run(entry)
        return list(cpu.ram[syms["sRandoTms"][1]:syms["sRandoTms"][1] + num_tms])

    orders = {seed: order(seed) for seed in seeds}
    check("tms: a permutation of all 50",
          all(sorted(o) == list(range(num_tms)) for o in orders.values()),
          next((f"${s:08X} is not" for s, o in orders.items()
                if sorted(o) != list(range(num_tms))), ""))
    check("tms: not the identity",
          all(any(v != i for i, v in enumerate(o)) for o in orders.values()))
    check("tms: different seeds give different orders",
          len({tuple(o) for o in orders.values()}) == len(orders))

    def remap(tm_offset, off_flag=None):
        bank, entry = syms["RandoRemapTm"]
        cpu = Cpu(rom, bank)
        for i, ch in enumerate(b"RAND"):
            cpu.ram[syms["sRandoMagic"][1] + i] = ch
        for i in range(4):
            cpu.ram[syms["sRandoSeed"][1] + i] = (seeds[0] >> (8 * i)) & 0xFF
        if off_flag:
            cpu.ram[syms["sRandoFlags"][1]] = 1 << RANDO_FLAG_BITS[off_flag]
        cpu.e = tm_offset
        cpu.run(entry)
        return cpu.e

    table = orders[seeds[0]]
    check("tms: the lookup agrees with the table",
          all(remap(i) == table[i] for i in range(num_tms)))
    check("tms: gate off hands over the same tm",
          all(remap(i, "FLAG_RANDOM_TMS_OFF") == i for i in range(num_tms)))
    check("tms: the wild gate does not reach them",
          all(remap(i, "FLAG_RANDOM_WILD_OFF") == table[i] for i in range(num_tms)))


def gate_test(rom, syms, seed, species_map, trainer_map, pool, index_of, check):
    """Each category flag must switch off its own mapping and no other.

    The flags are stored inverted -- set means off -- so that a zero byte reads
    as everything on, which is what saves made before they existed hold.
    """
    def prime(cpu, off_flag):
        for i, ch in enumerate(b"RAND"):
            cpu.ram[syms["sRandoMagic"][1] + i] = ch
        for i in range(4):
            cpu.ram[syms["sRandoSeed"][1] + i] = (seed >> (8 * i)) & 0xFF
        if off_flag:
            cpu.ram[syms["sRandoFlags"][1]] = 1 << RANDO_FLAG_BITS[off_flag]

    def wild(species, off_flag=None):
        bank, entry = syms["ApplyRandoSpecies"]
        cpu = Cpu(rom, bank)
        prime(cpu, off_flag)
        cpu.e = species
        cpu.run(entry)
        return cpu.e

    def trainer(species, off_flag=None):
        bank, entry = syms["RandoRemapPartySpecies"]
        cpu = Cpu(rom, bank)
        prime(cpu, off_flag)
        cpu.ram[syms["wCurOpponent"][1]] = 0  # not a rival, so the starter carry is off
        cpu.ram[syms["wCurPartySpecies"][1]] = species
        cpu.run(entry)
        return cpu.ram[syms["wCurPartySpecies"][1]]

    def starter(constant, off_flag=None):
        bank, entry = syms["RandoStarterSpecies"]
        cpu = Cpu(rom, bank)
        prime(cpu, off_flag)
        cpu.a = constant
        cpu.run(entry)
        return cpu.a

    sample = pool[:4] + pool[-4:]
    starter1 = index_of["CHARMANDER"]  # STARTER1 is the constant for it

    check("gate: wild off leaves the species alone",
          all(wild(s, "FLAG_RANDOM_WILD_OFF") == s for s in sample))
    check("gate: wild on still maps",
          all(wild(s) == species_map[s] for s in sample))
    check("gate: trainers off leaves opposing teams alone",
          all(trainer(s, "FLAG_RANDOM_TRAINERS_OFF") == s for s in sample))
    check("gate: trainers on still maps",
          all(trainer(s) == trainer_map[s] for s in sample))
    check("gate: starters off gives back the vanilla starter",
          starter(starter1, "FLAG_RANDOM_STARTERS_OFF") == starter1)
    check("gate: starters on still substitutes",
          starter(starter1) != starter1)

    # the bits must be independent, or one row on the options page silently
    # switches off another category
    check("gate: the wild flag does not reach opposing teams",
          all(trainer(s, "FLAG_RANDOM_WILD_OFF") == trainer_map[s] for s in sample))
    check("gate: the trainer flag does not reach the wild table",
          all(wild(s, "FLAG_RANDOM_TRAINERS_OFF") == species_map[s] for s in sample))
    check("gate: the starter flag reaches neither table",
          all(wild(s, "FLAG_RANDOM_STARTERS_OFF") == species_map[s] for s in sample)
          and all(trainer(s, "FLAG_RANDOM_STARTERS_OFF") == trainer_map[s]
                  for s in sample))


def new_game_test(rom, syms, check):
    """The new game gate must arm or disarm the randomizer every time.

    RandoNewGame itself shows the seed entry screen, which needs a real console,
    so the seed derivation is tested through RandoSeedFromBuffer -- the half that
    runs once the player has typed something.
    """
    magic, sseed = syms["sRandoMagic"][1], syms["sRandoSeed"][1]

    def seed_from(text):
        bank, entry = syms["RandoDeriveSeed"]
        cpu = Cpu(rom, bank)
        buf = syms["wStringBuffer"][1]
        encoded = bytes((0x80 + (ord(c) - ord("A"))) for c in text) + b"\x50"
        cpu.ram[buf:buf + len(encoded)] = encoded
        cpu.ram[syms["hRandomAdd"][1]] = 0x3C
        cpu.ram[syms["hRandomSub"][1]] = 0x91
        cpu.ram[syms["hLoadedROMBank"][1]] = bank
        cpu.run(entry)
        rendered = bytes(cpu.ram[syms["wStringBuffer"][1]:
                                 syms["wStringBuffer"][1] + 9])
        return (bytes(cpu.ram[magic:magic + 4]), bytes(cpu.ram[sseed:sseed + 4]),
                rendered)

    def decode(rendered):
        """Charmap: 'A' is $80, '@' terminates."""
        out = ""
        for b in rendered:
            if b == 0x50:
                break
            out += chr(ord("A") + b - 0x80) if 0x80 <= b <= 0x99 else "?"
        return out

    m, s, shown = seed_from("JOLTEON")
    check("seed: typed text is shown back unchanged", decode(shown) == "JOLTEON",
          f"showed {decode(shown)!r}")

    # A rolled seed is only useful if typing it back reproduces the same world.
    _, rolled_seed, rolled_shown = seed_from("")
    rolled_text = decode(rolled_shown)
    check("seed: rolled seed is typable", rolled_text.isalpha() and rolled_text,
          f"showed {rolled_text!r}")
    check("seed: rolled seed round-trips when typed back",
          seed_from(rolled_text)[1] == rolled_seed,
          f"{rolled_text!r} re-derives to a different seed")
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


def parse_starter_sets():
    """The emitted starter sets, as triples of species constant names."""
    text = (ROOT / "data/randomizer/species_pool.asm").read_text()
    sets = []
    for line in text.split("RandoStarterTriples::")[1].splitlines():
        s = line.split(";")[0].strip()
        if s.startswith("assert_table_length"):
            break
        m = re.match(r"^db\s+(\w+)\s*,\s*(\w+)\s*,\s*(\w+)$", s)
        if m:
            sets.append((m.group(1), m.group(2), m.group(3)))
    return sets


def starter_sets_test(rom, syms, seed, starters, check):
    """Re-derive the rules from the source data, not from the generator."""
    sys.path.insert(0, str(ROOT / "tools"))
    from generate_species_pool import (parse_base_stat_files, parse_species_types,
                                       parse_type_chart, make_beats, parse_evolutions,
                                       parse_species_constants, flatten,
                                       STARTER_SET_PIECES, EEVEELUTIONS)
    sets = parse_starter_sets()
    types = parse_species_types(parse_base_stat_files())
    beats = make_beats(types, parse_type_chart())
    evolved = {flatten(v) for v in parse_evolutions().values() if v}
    pieces = {tuple(m) for _, m in STARTER_SET_PIECES}
    generated = [s for s in sets if s not in pieces]
    legendary = {"MOLTRES", "ARTICUNO", "ZAPDOS", "MEWTWO", "MEW"}

    check("starters: 151 sets emitted", len(sets) == 151, f"got {len(sets)}")
    bad = [s for s in generated
           if not (beats(s[0], s[1]) and beats(s[1], s[2]) and beats(s[2], s[0]))]
    check("starters: every generated set is a real cycle", not bad,
          "; ".join(" > ".join(s) for s in bad[:3]))
    pairs = [frozenset((s[i], s[j])) for s in sets
             for i in range(3) for j in range(i + 1, 3)]
    check("starters: no pair of species repeats across sets",
          len(pairs) == len(set(pairs)))
    check("starters: no evolved species outside the Eeveelutions",
          all(flatten(x) not in evolved or x in EEVEELUTIONS
              for s in generated for x in s))
    check("starters: Ditto never appears", all("DITTO" not in s for s in sets))
    check("starters: legendaries only in their own set",
          all(not (legendary & set(s)) for s in generated))

    # and the set the rom actually chose is one of them
    name_of = parse_species_constants()
    index = {v: k for k, v in name_of.items()}
    chosen = tuple(name_of.get(b, f"?{b}") for b in starters)
    check("starters: the chosen set is one of the emitted sets", chosen in set(sets),
          " > ".join(chosen))
    check("starters: same seed picks the same set",
          generate(rom, syms, seed)[2] == starters)


def rival_chain_test(rom, syms, seed, starters, check):
    """The rival's starter must follow its own evolution line across his battles."""
    sys.path.insert(0, str(ROOT / "tools"))
    from generate_species_pool import parse_species_constants
    name_of = parse_species_constants()
    index = {v: k for k, v in name_of.items()}
    bank, entry = syms["RandoRemapPartySpecies"][0], syms["RandoRemapPartySpecies"][1]

    def call(species, opponent, rival_starter):
        cpu = Cpu(rom, bank)
        for i, ch in enumerate(b"RAND"):
            cpu.ram[syms["sRandoMagic"][1] + i] = ch
        for i in range(4):
            cpu.ram[syms["sRandoSeed"][1] + i] = (seed >> (8 * i)) & 0xFF
        cpu.ram[syms["hLoadedROMBank"][1]] = bank
        cpu.ram[syms["wCurOpponent"][1]] = opponent
        cpu.ram[syms["wRivalStarter"][1]] = rival_starter
        cpu.ram[syms["wCurPartySpecies"][1]] = species
        cpu.run(entry)
        return cpu.ram[syms["wCurPartySpecies"][1]]

    # trainer class ids from the constants, offset into opponent ids
    tc = (ROOT / "constants/trainer_constants.asm").read_text()
    off = int(re.search(r"DEF OPP_ID_OFFSET\s+EQU\s+(\d+)", tc).group(1))
    rival2 = off + int(re.search(r"trainer_const RIVAL2\s*;\s*\$([0-9A-Fa-f]+)", tc).group(1), 16)

    his = starters[0]  # the calls below say he took STARTER1
    stage1 = call(index["CHARMELEON"], rival2, index["CHARMANDER"])
    stage2 = call(index["CHARIZARD"], rival2, index["CHARMANDER"])
    base = call(index["CHARMANDER"], rival2, index["CHARMANDER"])

    evo = parse_starter_evolutions(rom, syms)
    check("rival: base stage is his starter", base == his,
          f"{name_of.get(base)} vs {name_of.get(his)}")
    check("rival: second stage is its evolution", stage1 == evo(his),
          f"{name_of.get(stage1)} vs {name_of.get(evo(his))}")
    check("rival: third stage is the one after", stage2 == evo(evo(his)),
          f"{name_of.get(stage2)} vs {name_of.get(evo(evo(his)))}")
    # a non-rival opponent must not get the substitution
    ordinary = call(index["CHARMELEON"], 0, index["CHARMANDER"])
    check("rival: ordinary trainers are unaffected", ordinary != stage1 or evo(his) == 0)


def parse_starter_evolutions(rom, syms):
    """RandoEvolvesTo straight out of the rom."""
    bank, addr = syms["RandoEvolvesTo"]
    base = bank * 0x4000 + (addr - 0x4000)
    def evo(species):
        nxt = rom[base + species]
        return nxt if nxt else species
    return evo


def options_row_test(check):
    """The RANDOM row's "bit set" x must sit on ON, not OFF.

    Options3XPosBitData lists the bit-set x position first, and SetSingleBitOption
    treats it as "cursor here means the bit is set". Getting it the wrong way round
    shows ON for a cleared bit, so the menu reads ON while the feature is off --
    and nothing in the rom tests can see it.
    """
    src = (ROOT / "engine/menus/options_menu3.asm").read_text()

    m = re.search(r'next\s+"( RANDOM:.*?)@?"', src)
    if not m:
        check("options: RANDOM row present", False, "row text not found")
        return
    row = m.group(1)
    on_col = row.index("ON")

    m = re.search(r"db\s+(\d+)\s*,\s*(\d+)\s*,\s*BIT_RANDOMIZER", src)
    if not m:
        check("options: RANDOM bit data present", False, "table entry not found")
        return
    bit_set_x, bit_clear_x = int(m.group(1)), int(m.group(2))

    # text is placed at hlcoord 1, so the cursor column equals the string index
    check("options: ON is the bit-set position", bit_set_x == on_col,
          f"bit-set x is {bit_set_x}, ON is at column {on_col}")
    check("options: OFF is the bit-clear position",
          bit_clear_x == row.index("OFF"),
          f"bit-clear x is {bit_clear_x}, OFF is at column {row.index('OFF')}")

    # The seed screen writes its hint on row 3, where the entry underscores
    # start at column 10. Anything longer than 9 characters gets overwritten.
    # Rod encounters set wCurOpponent and are remapped in InitOpponent with the
    # statics. Remapping in RodResponse as well would map them twice.
    effects = (ROOT / "engine/items/item_effects.asm").read_text()
    rod = effects.split("RodResponse:")[1].split("FishingInit:")[0]
    check("rods: mapped once, in InitOpponent", "Rando" not in rod,
          "RodResponse randomizes as well")

    naming = (ROOT / "engine/menus/naming_screen.asm").read_text()
    m = re.search(r'SeedBlankString:\s*\n\s*db\s+"([^"]*)@"', naming)
    if not m:
        check("seed screen: hint string present", False, "not found")
        return
    hint = m.group(1)
    check("seed screen: hint fits before the entry field", len(hint) <= 9,
          f'"{hint}" is {len(hint)} chars, only 9 columns are free')

    # The Game Corner draws its prize list from ROM rather than through
    # _GivePokemon, so each displayed name needs its own remap. wPrize1-3 must
    # stay vanilla: the price and the level are keyed to the slot's own species.
    prizes = (ROOT / "engine/events/prize_menu.asm").read_text()
    menu = re.split(r"^\.putMonName", prizes, flags=re.M)[1].split(".putNoThanksText")[0]
    check("prizes: all three menu names are remapped",
          menu.count("RandoRemapNamedWild") == 3,
          f"{menu.count('RandoRemapNamedWild')} of 3 names remapped")
    check("prizes: menu leaves wPrize1-3 vanilla",
          not re.search(r"ld\s+\[wPrize[123]\]", prizes),
          "something writes back to a wPrize slot")

    # The confirm prompt names the remapped mon but must hand the slot's own
    # species to GetPrizeMonLevel, which has no terminator to stop a bad key.
    confirm = re.split(r"^\.getMonName", prizes, flags=re.M)[1].split(".givePrize")[0]
    check("prizes: confirm prompt restores the vanilla species",
          "RandoRemapNamedWild" in confirm
          and confirm.rindex("ld [wNamedObjectIndex], a")
              > confirm.index("RandoRemapNamedWild"),
          "wNamedObjectIndex is left remapped for the level lookup")


def main():
    rom_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "pokered.gbc"
    sym_path = rom_path.with_suffix(".sym")
    rom = rom_path.read_bytes()
    syms = load_symbols(sym_path)
    pool, win_lo, win_hi, hm_learners, anchors, item_pool = parse_pool()
    poolset = set(pool)
    pos_of = {s: i for i, s in enumerate(pool)}

    def in_window(original, replacement):
        """The replacement must sit inside the original position's window."""
        return win_lo[pos_of[original]] <= pos_of[replacement] <= win_hi[pos_of[original]]


    sys.path.insert(0, str(ROOT / "tools"))
    from generate_species_pool import parse_species_constants, LEGENDARY_DEX
    name_of = parse_species_constants()
    index_of = {v: k for k, v in name_of.items()}
    legendary_names = {n: index_of[n] for n in
                       ("MEWTWO", "MEW", "ARTICUNO", "ZAPDOS", "MOLTRES")}
    legendaries = set(legendary_names.values())
    assert len(LEGENDARY_DEX) == len(legendaries), "legendary lists disagree"

    failures = []

    def check(name, ok, detail=""):
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' -- ' + detail) if detail and not ok else ''}")
        if not ok:
            failures.append(name)

    print(f"rom: {rom_path.name}   pool: {len(pool)} species\n")

    seeds = [0x00000001, 0x12345678, 0xDEADBEEF, 0x0000FFFF, 0xA5A5A5A5]
    maps, trainer_maps, starter_sets = {}, {}, {}
    for seed in seeds:
        m, mt, starters, steps = generate(rom, syms, seed)
        maps[seed] = m
        trainer_maps[seed] = mt
        starter_sets[seed] = starters
        print(f"seed ${seed:08X}  ({steps:,} instructions)")
        images = [m[s] for s in pool]
        check("bijection over pool", sorted(images) == sorted(pool),
              f"{len(set(images))} distinct of {len(pool)}")
        check("no value outside pool", all(v in poolset for v in images))
        check("replacement within base-stat window",
              all(in_window(s, m[s]) for s in pool))
        non_pool = [i for i in range(1, 191) if i not in poolset]
        check("non-pool species map to themselves", all(m[i] == i for i in non_pool))
        check("not the identity permutation", any(m[s] != s for s in pool),
              "every species mapped to itself")
        check("legendaries neither given out nor replaced",
              all(i not in poolset and m[i] == i for i in legendaries),
              ", ".join(n for n, i in legendary_names.items()
                        if i in poolset or m[i] != i))
        # the trainer table must be just as valid, and must not echo the wild one
        check("trainer table is its own bijection",
              sorted(mt[s] for s in pool) == sorted(pool))
        check("trainer table respects the window",
              all(in_window(s, mt[s]) for s in pool))
        check("trainer table differs from the wild one",
              sum(1 for s in pool if mt[s] != m[s]) > len(pool) // 2,
              f"only {sum(1 for s in pool if mt[s] != m[s])} of {len(pool)} differ")
        for bit, move in enumerate(("CUT", "SURF", "STRENGTH")):
            reachable = [hm_learners[m[pool[p]]] & (1 << bit) for p in anchors]
            check(f"a catchable mon can learn {move}", any(reachable))
        print()

    # wider sweep: the invariants must hold for every seed, not just five
    sweep_fail = []
    sweep = [(i * 0x9E3779B1) & 0xFFFFFFFF or 1 for i in range(1, 26)]
    for seed in sweep:
        m, mt, _, _ = generate(rom, syms, seed)
        images = [m[s] for s in pool]
        if sorted(images) != sorted(pool):
            sweep_fail.append((seed, "not a bijection"))
        if any(not in_window(s, m[s]) for s in pool):
            sweep_fail.append((seed, "left its window"))
        for bit, move in enumerate(("CUT", "SURF", "STRENGTH")):
            if not any(hm_learners[m[pool[p]]] & (1 << bit) for p in anchors):
                sweep_fail.append((seed, f"no {move} user"))
    check(f"sweep of {len(sweep)} seeds holds every invariant", not sweep_fail,
          "; ".join(f"${s:08X} {w}" for s, w in sweep_fail[:4]))

    m1, _, _, _ = generate(rom, syms, 0x12345678)
    check("deterministic (same seed twice)", m1 == maps[0x12345678])
    distinct = len({maps[s] for s in seeds})
    check("different seeds give different maps", distinct == len(seeds),
          f"only {distinct} distinct maps from {len(seeds)} seeds")

    print("\nwild encounter remap")
    wild_data_test(rom, syms, 0x12345678, maps[0x12345678], check)

    print("\nsingle species lookup (rod hook)")
    apply_species_test(rom, syms, 0x12345678, maps[0x12345678],
                       trainer_maps[0x12345678], pool, check)

    print("\ninverse lookup (prize king)")
    unmap_test(rom, syms, 0x12345678, maps[0x12345678], pool, check)

    print("\nground and hidden items")
    item_test(rom, syms, 0x12345678, item_pool, parse_item_constants(), check)

    print("\ntm order")
    tm_test(rom, syms, seeds, check)

    print("\ncategory gates")
    gate_test(rom, syms, 0x12345678, maps[0x12345678],
              trainer_maps[0x12345678], pool, index_of, check)

    print("\nnew game gate")
    new_game_test(rom, syms, check)

    print("\nstarter sets")
    starter_sets_test(rom, syms, 0x12345678, starter_sets[0x12345678], check)

    print("\nrival starter continuity")
    rival_chain_test(rom, syms, 0x12345678, starter_sets[0x12345678], check)

    print("\noptions row wiring")
    options_row_test(check)

    fixed = sum(1 for s in pool if maps[0x12345678][s] == s)
    print(f"\n  (seed $12345678 leaves {fixed}/{len(pool)} species unchanged)")

    print(f"\n{'ALL CHECKS PASSED' if not failures else 'FAILURES: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
