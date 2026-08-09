# romtest

Boots a built ROM headlessly, loads a save, walks around the overworld and reports
crashes. Complements `tools/test_randomizer.py`, which interprets individual
randomizer routines in isolation; this runs the whole game — interrupts, bank
switching, map loading, audio — and so catches the things a routine-level test
cannot see.

## Building and running

    tools/romtest/build.sh                 # clones binjgb on first use, then compiles
    tools/romtest/romtest pokered.gbc --sav pokered.sav

Exit status is 0 if the run was clean. `--sav` is effectively required: the harness
drives CONTINUE, and cannot get through the naming screen and Oak's intro that a
new game needs.

The emulator core is [binjgb](https://github.com/binji/binjgb) (MIT). It is cloned
into `tools/romtest/binjgb` on demand and pinned to a commit; nothing third party
is checked in.

## What it checks

- **Stale map header.** The map header is copied into WRAM pointers and all, and
  WRAM is saved, so a save written by a different build of the ROM names addresses
  that have since moved. `romtest` compares `wCurMapDataPtr`, `wCurMapTextPtr` and
  `wCurMapScriptPtr` against the header the ROM actually holds for the current map.

  This is checked directly rather than by waiting for a crash, because whether a
  stale pointer crashes depends on what happens to be in the registers when the map
  script next runs — it can pass a hundred steps and then wedge.

- **Invalid opcodes**, which mean control has ended up in data.
- **Lockups**, meaning the PC sat still long enough that `DelayFrame` is clearly
  halting for a VBlank that will never arrive.

To guard against save-compatibility regressions, keep a save or two from an earlier
release and run them against each new build.

## Debugging a failure

The failure report names a tick. Re-run with `--trace-from` set a little before it
to get a symbolised, per-function instruction trace of the approach — single
stepping is far too slow to leave on for a whole run.

    tools/romtest/romtest pokered.gbc --sav old.sav --trace-from 28500000

`--watch ADDR` reports every change to the four bytes at ADDR along with the
instruction responsible, which is how you find whatever is smashing the stack.
`--break PC` prints the registers each time an address is reached. `--screenshot
FILE` writes a PNG of the last frame, which is usually the quickest way to see that
the harness is somewhere unexpected.

Symbols come from the `.sym` the build emits, found next to the ROM by default or
named with `--sym`.
