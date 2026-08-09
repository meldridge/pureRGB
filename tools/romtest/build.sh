#!/bin/sh
# Builds romtest against binjgb, cloning it on first use.
#
# binjgb is only ever fetched, never vendored, so nothing third party lands in
# this repo. It is pinned so a failing run means the rom changed, not the
# emulator.
set -eu

BINJGB_COMMIT=c60e138da5a795ebb55e56b11b7e90024e41112c
BINJGB_URL=https://github.com/binji/binjgb.git

here=$(cd "$(dirname "$0")" && pwd)
src="$here/binjgb"

if [ ! -d "$src" ]; then
	echo "cloning binjgb into $src"
	git clone --quiet "$BINJGB_URL" "$src"
fi
(cd "$src" && git fetch --quiet --depth 1 origin "$BINJGB_COMMIT" 2>/dev/null || true)
(cd "$src" && git checkout --quiet "$BINJGB_COMMIT")

# emulator-debug.c is emulator.c with the register and raw-read hooks compiled in.
cc -O2 -I"$src/src" -o "$here/romtest" \
	"$here/romtest.c" \
	"$src/src/emulator-debug.c" \
	"$src/src/common.c" \
	"$src/src/memory.c" \
	-lm

echo "built $here/romtest"
