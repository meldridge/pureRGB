; PureRGBnote: ADDED: randomizer mode.
; Species are remapped at lookup time instead of rewriting the ROM tables, so a
; single build plays any seed. The permutation lives in sram (wram bank 1 is
; full and the game never switches SVBK) and is rebuilt whenever sRandoMapSeed
; disagrees with wRandoSeed, so callers never have to prime it.
;
; Species are passed in e because rst _Bankswitch clobbers a and bc.

INCLUDE "data/randomizer/species_pool.asm"

; nz if this save is a randomizer game. Clobbers a, hl.
RandoEnabled::
	ld a, [wRandoSeed]
	ld hl, wRandoSeed + 1
	or [hl]
	inc hl
	or [hl]
	inc hl
	or [hl]
	ret

; e = species in, e = species out.
; Left alone if the randomizer is off, or if the species is NO_MON or an index
; outside the pool.
ApplyRandoSpecies::
	push hl
	push bc
	call RandoEnabled
	jr z, .done
	ld a, e
	and a
	jr z, .done
	cp NUM_POKEMON_INDEXES + 1
	jr nc, .done
	call RandoSramOn
	push de
	call EnsureSpeciesMap
	pop de
	ld hl, sRandoMap
	ld d, 0
	add hl, de
	ld e, [hl]
	call RandoSramOff
.done
	pop bc
	pop hl
	ret

; Remaps b species bytes starting at hl, stepping c bytes between them.
; Used on the wild data ram copies, which interleave levels and species.
RandoRemapBuffer::
	push hl
	call RandoEnabled
	pop hl
	ret z
.loop
	ld e, [hl]
	call ApplyRandoSpecies
	ld a, e
	ld [hl], a
	ld a, c
	add l
	ld l, a
	jr nc, .noCarry
	inc h
.noCarry
	dec b
	jr nz, .loop
	ret

RandoSramOn::
	ld a, RAMG_SRAM_ENABLE
	ld [rRAMG], a
	ld a, BMODE_ADVANCED
	ld [rBMODE], a
	ld a, BANK("Sprite Buffers")
	ld [rRAMB], a
	ret

RandoSramOff::
	ld a, BMODE_SIMPLE
	ld [rBMODE], a
	ASSERT RAMG_SRAM_DISABLE == BMODE_SIMPLE
	ld [rRAMG], a
	ret

; Assumes sram is already enabled.
EnsureSpeciesMap::
	ld hl, wRandoSeed
	ld de, sRandoMapSeed
	ld c, 4
.compare
	ld a, [de]
	cp [hl]
	jr nz, GenerateSpeciesMap
	inc hl
	inc de
	dec c
	jr nz, .compare
	ret

GenerateSpeciesMap:
	ld hl, wRandoSeed
	ld de, sRandoRngState
	ld c, 4
	call RandoCopyBytes
	ld hl, wRandoSeed
	ld de, sRandoMapSeed
	ld c, 4
	call RandoCopyBytes

	; species outside the pool map to themselves
	ld hl, sRandoMap
	xor a
.identity
	ld [hli], a
	inc a
	cp NUM_POKEMON_INDEXES + 1
	jr nz, .identity

	ld hl, RandoPool
	ld de, sRandoShuffle
	ld c, RANDO_POOL_SIZE
	call RandoCopyBytes

	call ShuffleBuckets

	; pool entry i is replaced by whatever landed in shuffle slot i
	ld hl, RandoPool
	ld de, sRandoShuffle
	ld c, RANDO_POOL_SIZE
.assign
	push bc
	ld a, [hli]
	push hl
	ld hl, sRandoMap
	ld b, 0
	ld c, a
	add hl, bc
	ld a, [de]
	ld [hl], a
	pop hl
	pop bc
	inc de
	dec c
	jr nz, .assign
	ret

; Fisher-Yates over each bucket of sRandoShuffle independently, so a species'
; replacement stays inside its own base-stat band.
ShuffleBuckets:
	ld b, 0
.nextBucket
	push bc
	ld hl, RandoBucketBounds
	ld d, 0
	ld e, b
	add hl, de
	ld a, [hli]
	ld d, [hl]
	ld e, a
	call ShuffleRange
	pop bc
	inc b
	ld a, b
	cp RANDO_NUM_BUCKETS
	jr nz, .nextBucket
	ret

; Shuffles sRandoShuffle[e .. d-1].
ShuffleRange:
	ld a, d
	sub e
	cp 2
	ret c ; 0 or 1 entries, nothing to do
	ld a, e
	ld [sRandoLo], a
	ld a, d
	dec a
	ld [sRandoI], a
.loop
	; pick j somewhere in [lo, i]
	ld a, [sRandoI]
	ld hl, sRandoLo
	sub [hl]
	inc a
	ld c, a
	call RandoRandRange
	ld hl, sRandoLo
	add [hl]
	ld [sRandoJ], a

	; swap shuffle[i] and shuffle[j]
	ld hl, sRandoShuffle
	ld a, [sRandoI]
	ld d, 0
	ld e, a
	add hl, de
	push hl
	ld hl, sRandoShuffle
	ld a, [sRandoJ]
	ld d, 0
	ld e, a
	add hl, de
	pop de
	ld a, [hl]
	ld b, a
	ld a, [de]
	ld [hl], a
	ld a, b
	ld [de], a

	ld hl, sRandoI
	dec [hl]
	ld a, [sRandoLo]
	cp [hl]
	jr c, .loop
	ret

; a = random value in [0, c). c must be nonzero.
RandoRandRange:
	push bc
	call RandoRand
	pop bc
.reduce
	cp c
	ret c
	sub c
	jr .reduce

; a = next byte of the 32 bit xorshift generator. Clobbers bc, de, hl.
RandoRand:
	ld c, 13
	call RandoShiftTempLeft
	ld c, 17
	call RandoShiftTempRight
	ld c, 5
	call RandoShiftTempLeft
	ld a, [sRandoRngState]
	ret

RandoShiftTempLeft:
	call RandoStateToTemp
.shift
	ld hl, sRandoRngTemp
	sla [hl]
	inc hl
	rl [hl]
	inc hl
	rl [hl]
	inc hl
	rl [hl]
	dec c
	jr nz, .shift
	jr RandoXorTempIntoState

RandoShiftTempRight:
	call RandoStateToTemp
.shift
	ld hl, sRandoRngTemp + 3
	srl [hl]
	dec hl
	rr [hl]
	dec hl
	rr [hl]
	dec hl
	rr [hl]
	dec c
	jr nz, .shift
	jr RandoXorTempIntoState

RandoStateToTemp:
	push bc
	ld hl, sRandoRngState
	ld de, sRandoRngTemp
	ld c, 4
	call RandoCopyBytes
	pop bc
	ret

RandoXorTempIntoState:
	ld hl, sRandoRngTemp
	ld de, sRandoRngState
	ld c, 4
.loop
	ld a, [de]
	xor [hl]
	ld [de], a
	inc hl
	inc de
	dec c
	jr nz, .loop
	ret

; copies c bytes from hl to de
RandoCopyBytes:
	ld a, [hli]
	ld [de], a
	inc de
	dec c
	jr nz, RandoCopyBytes
	ret
