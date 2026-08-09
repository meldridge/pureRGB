/* romtest -- boot a built rom headlessly and look for crashes.
 *
 * Runs the game as it actually runs -- interrupts, banking, map loading -- rather
 * than interpreting routines in isolation the way tools/test_randomizer.py does.
 *
 * Usage:
 *   romtest ROM [options]
 *     --sav FILE          load a save file into cartridge ram first
 *     --sym FILE          symbol file (default: ROM with .sym)
 *     --steps N           overworld steps to walk (default 60)
 *     --screenshot FILE   write a png of the last frame
 *     --trace-from TICKS  single-step from TICKS on, keeping an instruction ring
 *     --watch ADDR        report every change to the four bytes at ADDR (hex)
 *     --break PC          report registers every time PC is reached (hex)
 *
 * Exit status is 0 if the run was clean, non-zero if it was not.
 *
 * The failure report names a tick. Re-run with --trace-from a little before it to
 * get a symbolised instruction trace of the approach; single-stepping is far too
 * slow to leave on for a whole run.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "emulator-debug.h"

/* Addresses the harness needs. All stable pokered/pureRGB symbols. */
#define ADDR_IN_GAME_FLAGS 0xCD61 /* wNewInGameFlags, bit 0 = IN_GAME */
#define ADDR_CUR_MAP 0xD366
#define ADDR_Y_COORD 0xD369
#define ADDR_X_COORD 0xD36A
#define ADDR_MAP_SCRIPT_PTR 0xD376
#define ADDR_IS_IN_BATTLE 0xD057
#define ADDR_LOADED_ROM_BANK 0xFFB8
#define ADDR_VBLANK_OCCURRED 0xFFD6

#define TRACE_LEN (1 << 21)
#define LOCKUP_FRAMES 150 /* pc frozen this long means DelayFrame is not waking */

static Emulator* e;
static JoypadButtons g_buttons;
static u16 g_trace_pc[TRACE_LEN];
static u8 g_trace_bank[TRACE_LEN];
static u16 g_trace_sp[TRACE_LEN];
static size_t g_trace_n;
static Ticks g_trace_from;
static u16 g_watch, g_break_pc;
static u32 g_watch_val;
static u16 g_last_pc;
static int g_pc_frozen;

static void joyp_cb(JoypadButtons* joyp, void* user) { *joyp = g_buttons; }
static u8 rd(Address a) { return emulator_read_u8_raw(e, a); }
static u16 rd16(Address a) { return rd(a) | (rd(a + 1) << 8); }

/* --- symbols ------------------------------------------------------------- */

typedef struct { u16 addr; char name[64]; } Sym;
typedef struct { Sym* syms; size_t n, cap; } Bank;
static Bank g_banks[0x100];

static int sym_cmp(const void* a, const void* b) {
  u16 x = ((const Sym*)a)->addr, y = ((const Sym*)b)->addr;
  return x < y ? -1 : x > y ? 1 : 0;
}

static void load_symbols(const char* path) {
  FILE* f = fopen(path, "r");
  if (!f) { printf("note: no symbol file at %s\n", path); return; }
  char line[256];
  while (fgets(line, sizeof(line), f)) {
    unsigned bank, addr;
    char name[64];
    if (sscanf(line, "%x:%x %63s", &bank, &addr, name) != 3) continue;
    if (bank > 0xff) continue;
    Bank* b = &g_banks[bank];
    if (b->n == b->cap) {
      b->cap = b->cap ? b->cap * 2 : 256;
      b->syms = realloc(b->syms, b->cap * sizeof(Sym));
    }
    b->syms[b->n].addr = (u16)addr;
    snprintf(b->syms[b->n].name, sizeof(b->syms[b->n].name), "%s", name);
    b->n++;
  }
  fclose(f);
  int i;
  for (i = 0; i < 0x100; i++)
    if (g_banks[i].n) qsort(g_banks[i].syms, g_banks[i].n, sizeof(Sym), sym_cmp);
}

/* Nearest preceding symbol. Addresses below $4000 are always in bank 0. */
static const char* symbolise(u8 bank, u16 addr, int* offset) {
  Bank* b = &g_banks[addr < 0x4000 ? 0 : bank];
  if (!b->n) { *offset = 0; return NULL; }
  size_t lo = 0, hi = b->n;
  while (lo < hi) {
    size_t mid = (lo + hi) / 2;
    if (b->syms[mid].addr <= addr) lo = mid + 1; else hi = mid;
  }
  if (lo == 0) { *offset = 0; return NULL; }
  *offset = addr - b->syms[lo - 1].addr;
  return b->syms[lo - 1].name;
}

static int find_symbol(const char* name, u8* bank, u16* addr) {
  int b;
  size_t i;
  for (b = 0; b < 0x100; b++)
    for (i = 0; i < g_banks[b].n; i++)
      if (!strcmp(g_banks[b].syms[i].name, name)) {
        *bank = (u8)b;
        *addr = g_banks[b].syms[i].addr;
        return 1;
      }
  return 0;
}

static void print_loc(const char* prefix, u8 bank, u16 addr) {
  int off;
  const char* name = symbolise(bank, addr, &off);
  if (name && off) printf("%s%02X:%04X  %s+%d\n", prefix, bank, addr, name, off);
  else if (name) printf("%s%02X:%04X  %s\n", prefix, bank, addr, name);
  else printf("%s%02X:%04X\n", prefix, bank, addr);
}

/* --- png ----------------------------------------------------------------- */

static u32 crc_table[256];
static void crc_init(void) {
  u32 n, k, c;
  for (n = 0; n < 256; n++) {
    c = n;
    for (k = 0; k < 8; k++) c = (c & 1) ? 0xedb88320u ^ (c >> 1) : c >> 1;
    crc_table[n] = c;
  }
}
static u32 crc32_buf(const u8* p, size_t n, u32 c) {
  size_t i;
  for (i = 0; i < n; i++) c = crc_table[(c ^ p[i]) & 0xff] ^ (c >> 8);
  return c;
}
static void put32(FILE* f, u32 v) {
  fputc(v >> 24, f); fputc(v >> 16, f); fputc(v >> 8, f); fputc(v, f);
}
static void chunk(FILE* f, const char* tag, const u8* data, size_t n) {
  put32(f, (u32)n);
  u32 c = crc32_buf((const u8*)tag, 4, 0xffffffffu);
  c = crc32_buf(data, n, c);
  fwrite(tag, 1, 4, f);
  fwrite(data, 1, n, f);
  put32(f, c ^ 0xffffffffu);
}

/* Stored-deflate png, so the tool needs no zlib. */
static void write_screenshot(const char* path) {
  FrameBuffer* fb = emulator_get_frame_buffer(e);
  size_t raw_n = (size_t)SCREEN_HEIGHT * (1 + SCREEN_WIDTH * 3);
  u8* raw = malloc(raw_n);
  size_t o = 0;
  int x, y;
  for (y = 0; y < SCREEN_HEIGHT; y++) {
    raw[o++] = 0;
    for (x = 0; x < SCREEN_WIDTH; x++) {
      RGBA p = (*fb)[y * SCREEN_WIDTH + x];
      raw[o++] = p & 0xff; raw[o++] = (p >> 8) & 0xff; raw[o++] = (p >> 16) & 0xff;
    }
  }
  u32 a = 1, b = 0;
  size_t i;
  for (i = 0; i < raw_n; i++) { a = (a + raw[i]) % 65521; b = (b + a) % 65521; }

  size_t idat_cap = raw_n + raw_n / 65535 * 5 + 32;
  u8* idat = malloc(idat_cap);
  size_t n = 0;
  idat[n++] = 0x78; idat[n++] = 0x01;
  for (i = 0; i < raw_n;) {
    size_t len = raw_n - i > 65535 ? 65535 : raw_n - i;
    idat[n++] = (i + len >= raw_n) ? 1 : 0;
    idat[n++] = len & 0xff; idat[n++] = len >> 8;
    idat[n++] = ~len & 0xff; idat[n++] = (~len >> 8) & 0xff;
    memcpy(idat + n, raw + i, len);
    n += len; i += len;
  }
  idat[n++] = b >> 8; idat[n++] = b & 0xff;
  idat[n++] = a >> 8; idat[n++] = a & 0xff;

  FILE* f = fopen(path, "wb");
  if (!f) { free(raw); free(idat); return; }
  static const u8 sig[8] = {0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n'};
  fwrite(sig, 1, 8, f);
  u8 ihdr[13];
  ihdr[0] = 0; ihdr[1] = 0; ihdr[2] = SCREEN_WIDTH >> 8; ihdr[3] = SCREEN_WIDTH & 0xff;
  ihdr[4] = 0; ihdr[5] = 0; ihdr[6] = SCREEN_HEIGHT >> 8; ihdr[7] = SCREEN_HEIGHT & 0xff;
  ihdr[8] = 8; ihdr[9] = 2; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  chunk(f, "IHDR", ihdr, sizeof(ihdr));
  chunk(f, "IDAT", idat, n);
  chunk(f, "IEND", NULL, 0);
  fclose(f);
  free(raw); free(idat);
  printf("screenshot: %s\n", path);
}

/* --- reporting ----------------------------------------------------------- */

static void dump_trace(void) {
  if (!g_trace_n) {
    printf("  (no instruction trace; re-run with --trace-from to capture one)\n");
    return;
  }
  size_t n = g_trace_n < TRACE_LEN ? g_trace_n : TRACE_LEN;
  size_t start = g_trace_n < TRACE_LEN ? 0 : g_trace_n % TRACE_LEN;
  printf("  --- trace, one line per function entered ---\n");
  const char* prev = NULL;
  size_t i;
  for (i = 0; i < n; i++) {
    size_t k = (start + i) % TRACE_LEN;
    int off;
    const char* name = symbolise(g_trace_bank[k], g_trace_pc[k], &off);
    if (name == prev) continue;
    prev = name;
    print_loc("  ", g_trace_bank[k], g_trace_pc[k]);
  }
}

static void dump_state(void) {
  Registers r = emulator_get_registers(e);
  u8 bank = rd(ADDR_LOADED_ROM_BANK);
  print_loc("  pc  ", bank, r.PC);
  printf("  af=%02X bc=%04X de=%04X hl=%04X sp=%04X bank=%02X\n", r.A, r.BC,
         r.DE, r.HL, r.SP, bank);
  printf("  ie=%02X if=%02X lcdc=%02X ly=%02X vblank_occurred=%02X\n", rd(0xFFFF),
         rd(0xFF0F), rd(0xFF40), rd(0xFF44), rd(ADDR_VBLANK_OCCURRED));
  printf("  map=%02X pos=%02X,%02X script_ptr=%04X in_battle=%02X\n",
         rd(ADDR_CUR_MAP), rd(ADDR_Y_COORD), rd(ADDR_X_COORD),
         rd16(ADDR_MAP_SCRIPT_PTR), rd(ADDR_IS_IN_BATTLE));
  printf("  possible return addresses on the stack:\n");
  int i;
  for (i = 0; i < 12; i++) {
    u16 v = rd16(r.SP + i * 2);
    if (v < 0x8000) print_loc("    ", bank, v); /* anything else is not rom */
  }
}

/* --- map header check ---------------------------------------------------- */

static FileData g_rom;

static u8 rom_read(u8 bank, u16 addr) {
  size_t off = (size_t)bank * 0x4000 + (addr >= 0x4000 ? addr - 0x4000 : addr);
  return off < g_rom.size ? g_rom.data[off] : 0xff;
}
static u16 rom_read16(u8 bank, u16 addr) {
  return rom_read(bank, addr) | (rom_read(bank, addr + 1) << 8);
}

/* The map header is copied into wram, pointers and all, and wram is saved. A save
   written by a different build therefore names addresses that have since moved,
   which sends the next map script tick into the middle of an instruction. Whether
   that crashes depends on what happens to be in the registers, so check the
   invariant rather than waiting for a crash to show up. */
static int check_map_header(void) {
  u8 ptr_bank, banks_bank;
  u16 ptr_addr, banks_addr;
  if (!find_symbol("MapHeaderPointers", &ptr_bank, &ptr_addr) ||
      !find_symbol("MapHeaderBanks", &banks_bank, &banks_addr)) {
    printf("note: no map header symbols, skipping the header check\n");
    return 1;
  }
  u8 map = rd(ADDR_CUR_MAP);
  u8 header_bank = rom_read(banks_bank, banks_addr + map);
  u16 header = rom_read16(ptr_bank, ptr_addr + map * 2);

  static const struct { const char* name; u16 wram; u16 field; } fields[] = {
      {"data", 0xD372, 3}, {"text", 0xD374, 5}, {"script", 0xD376, 7}};
  int ok = 1;
  size_t i;
  for (i = 0; i < sizeof(fields) / sizeof(fields[0]); i++) {
    u16 live = rd16(fields[i].wram);
    u16 want = rom_read16(header_bank, header + fields[i].field);
    if (live != want) {
      printf("\nFAIL: map %02X has a stale %s pointer: wram says %04X, rom says "
             "%04X\n",
             map, fields[i].name, live, want);
      ok = 0;
    }
  }
  if (!ok)
    printf("  the map header in wram did not come from this rom -- a save from "
           "another build was loaded and never refreshed\n");
  return ok;
}

/* --- running ------------------------------------------------------------- */

enum { RUN_OK, RUN_INVALID_OPCODE, RUN_LOCKUP, RUN_STALE_HEADER };

static int step_frame(void) {
  Ticks until = emulator_get_ticks(e) + PPU_FRAME_TICKS;
  EmulatorEvent ev = 0;

  if (g_trace_from && emulator_get_ticks(e) >= g_trace_from) {
    while (emulator_get_ticks(e) < until) {
      Registers r = emulator_get_registers(e);
      u8 bank = rd(ADDR_LOADED_ROM_BANK);
      if (g_break_pc && r.PC == g_break_pc)
        printf("break %02X:%04X a=%02X bc=%04X de=%04X hl=%04X sp=%04X\n", bank,
               r.PC, r.A, r.BC, r.DE, r.HL, r.SP);
      size_t k = g_trace_n++ % TRACE_LEN;
      g_trace_pc[k] = r.PC; g_trace_bank[k] = bank; g_trace_sp[k] = r.SP;
      ev |= emulator_run_until(e, emulator_get_ticks(e) + 1);
      if (g_watch) {
        u32 now = rd16(g_watch) | ((u32)rd16(g_watch + 2) << 16);
        if (now != g_watch_val) {
          printf("watch %04X: %08X -> %08X at ", g_watch, g_watch_val, now);
          print_loc("", bank, r.PC);
          g_watch_val = now;
        }
      }
      if (ev & EMULATOR_EVENT_INVALID_OPCODE) break;
    }
  } else {
    ev = emulator_run_until(e, until);
  }

  if (ev & EMULATOR_EVENT_INVALID_OPCODE) return RUN_INVALID_OPCODE;

  /* A frozen pc across many frames means DelayFrame is halting for a vblank
     that never comes -- the game is wedged even though the emulator is fine. */
  Registers r = emulator_get_registers(e);
  if (r.PC == g_last_pc) {
    if (++g_pc_frozen >= LOCKUP_FRAMES) return RUN_LOCKUP;
  } else {
    g_pc_frozen = 0;
    g_last_pc = r.PC;
  }
  return RUN_OK;
}

static int run_frames(int n) {
  int i, rc;
  for (i = 0; i < n; i++)
    if ((rc = step_frame()) != RUN_OK) return rc;
  return RUN_OK;
}

static int press(JoypadButtons b, int hold, int gap) {
  int rc;
  g_buttons = b;
  if ((rc = run_frames(hold)) != RUN_OK) return rc;
  memset(&g_buttons, 0, sizeof(g_buttons));
  return run_frames(gap);
}

static void report(int rc, const char* stage) {
  const char* what = rc == RUN_INVALID_OPCODE
                         ? "executed an invalid opcode"
                         : "locked up waiting for a vblank";
  printf("\nFAIL: %s during %s, at tick %llu\n", what, stage,
         (unsigned long long)emulator_get_ticks(e));
  dump_trace();
  dump_state();
}

int main(int argc, char** argv) {
  const char *rom_path = NULL, *sav_path = NULL, *sym_path = NULL;
  const char* shot_path = NULL;
  int steps = 60, i;
  char sym_default[512];

  for (i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "--sav") && i + 1 < argc) sav_path = argv[++i];
    else if (!strcmp(argv[i], "--sym") && i + 1 < argc) sym_path = argv[++i];
    else if (!strcmp(argv[i], "--steps") && i + 1 < argc) steps = atoi(argv[++i]);
    else if (!strcmp(argv[i], "--screenshot") && i + 1 < argc) shot_path = argv[++i];
    else if (!strcmp(argv[i], "--trace-from") && i + 1 < argc)
      g_trace_from = strtoull(argv[++i], NULL, 0);
    else if (!strcmp(argv[i], "--watch") && i + 1 < argc)
      g_watch = (u16)strtoul(argv[++i], NULL, 16);
    else if (!strcmp(argv[i], "--break") && i + 1 < argc)
      g_break_pc = (u16)strtoul(argv[++i], NULL, 16);
    else if (argv[i][0] != '-' && !rom_path) rom_path = argv[i];
    else { printf("unknown argument: %s\n", argv[i]); return 2; }
  }
  if (!rom_path) { printf("usage: romtest ROM [--sav FILE] [--steps N]\n"); return 2; }

  crc_init();
  if (!sym_path) {
    const char* dot = strrchr(rom_path, '.');
    size_t base = dot ? (size_t)(dot - rom_path) : strlen(rom_path);
    snprintf(sym_default, sizeof(sym_default), "%.*s.sym", (int)base, rom_path);
    sym_path = sym_default;
  }
  load_symbols(sym_path);

  if (!SUCCESS(file_read(rom_path, &g_rom))) {
    printf("cannot read %s\n", rom_path);
    return 2;
  }

  EmulatorInit init;
  memset(&init, 0, sizeof(init));
  init.rom = g_rom;
  init.audio_frequency = 44100;
  init.audio_frames = 2048;
  e = emulator_new(&init);
  if (!e) { printf("emulator_new failed\n"); return 2; }
  emulator_set_joypad_callback(e, joyp_cb, NULL);

  if (sav_path && !SUCCESS(emulator_read_ext_ram_from_file(e, sav_path))) {
    printf("cannot read %s\n", sav_path);
    return 2;
  }

  JoypadButtons A = {0}, START = {0}, dirs[4];
  memset(dirs, 0, sizeof(dirs));
  A.A = TRUE; START.start = TRUE;
  dirs[0].up = TRUE; dirs[1].right = TRUE; dirs[2].down = TRUE; dirs[3].left = TRUE;

  int rc = run_frames(60);
  /* Alternate start and a to get through the intro, the title and the menu.
     wNewInGameFlags IN_GAME goes up in SpecialEnterMap, which is exactly when the
     overworld is live -- press anything past that and we would be opening menus. */
  for (i = 0; i < 400 && rc == RUN_OK && !(rd(ADDR_IN_GAME_FLAGS) & 1); i++) {
    rc = press(START, 6, 10);
    if (rc != RUN_OK || (rd(ADDR_IN_GAME_FLAGS) & 1)) break;
    rc = press(A, 6, 10);
  }
  if (rc != RUN_OK) { report(rc, "startup"); goto done; }
  if (!(rd(ADDR_IN_GAME_FLAGS) & 1)) {
    /* Starting a new game means the naming screen and Oak's intro, which button
       mashing cannot get through. Pass a save of somewhere in the overworld. */
    printf("\nFAIL: never reached the overworld. Pass --sav; this only drives "
           "CONTINUE, not a new game.\n");
    rc = RUN_LOCKUP;
    goto done;
  }
  rc = run_frames(90);
  if (rc != RUN_OK) { report(rc, "startup"); goto done; }

  printf("overworld: map=%02X pos=%02X,%02X script_ptr=%04X\n", rd(ADDR_CUR_MAP),
         rd(ADDR_Y_COORD), rd(ADDR_X_COORD), rd16(ADDR_MAP_SCRIPT_PTR));
  if (!check_map_header()) { rc = RUN_STALE_HEADER; goto done; }

  /* Walk a square. Turning every few steps keeps us off a wall, and crossing map
     boundaries is what forces map headers to be reloaded. */
  for (i = 0; i < steps; i++) {
    g_buttons = dirs[(i / 3) & 3];
    if ((rc = run_frames(24)) != RUN_OK) break;
    memset(&g_buttons, 0, sizeof(g_buttons));
    if ((rc = run_frames(8)) != RUN_OK) break;
  }
  if (rc != RUN_OK) {
    char stage[64];
    snprintf(stage, sizeof(stage), "overworld step %d", i);
    report(rc, stage);
    goto done;
  }
  if (!check_map_header()) { rc = RUN_STALE_HEADER; goto done; }
  printf("PASS: %d steps, map=%02X pos=%02X,%02X\n", steps, rd(ADDR_CUR_MAP),
         rd(ADDR_Y_COORD), rd(ADDR_X_COORD));

done:
  if (shot_path) write_screenshot(shot_path);
  return rc == RUN_OK ? 0 : 1;
}
