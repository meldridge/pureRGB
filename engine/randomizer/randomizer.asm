; PureRGBnote: ADDED: randomizer mode.
; Species are remapped at lookup time instead of rewriting the ROM tables, so a
; single build plays any seed. The permutation lives in sram (wram bank 1 is
; full and the game never switches SVBK) and is rebuilt whenever sRandoMapSeed
; disagrees with sRandoSeed, so callers never have to prime it.
;
; Species are passed in e because rst _Bankswitch clobbers a and bc.

INCLUDE "data/randomizer/species_pool.asm"

; nz if this save is a randomizer game. Requires sram enabled. Clobbers a, hl.
RandoEnabled::
	ld hl, sRandoMagic
	ld a, [hli]
	cp $52 ; "R"
	jr nz, .off
	ld a, [hli]
	cp $41 ; "A"
	jr nz, .off
	ld a, [hli]
	cp $4E ; "N"
	jr nz, .off
	ld a, [hli]
	cp $44 ; "D"
	jr nz, .off
	; magic is good, so a nonzero seed means a randomizer game
	ld a, [sRandoSeed]
	ld hl, sRandoSeed + 1
	or [hl]
	inc hl
	or [hl]
	inc hl
	or [hl]
	ret
.off
	xor a
	ret

; e = species in, e = species out.
; Left alone if the randomizer is off, or if the species is NO_MON or an index
; outside the pool.
ApplyRandoSpecies::
	push hl
	push bc
	call RandoSramOn
	call RandoEnabled
	jr z, .sramOff
	call RandoMapSpecies
.sramOff
	call RandoSramOff
	pop bc
	pop hl
	ret

; e = species in, e = species out.
; Assumes sram is enabled and the randomizer is already known to be on.
RandoMapSpecies:
	ld a, e
	and a
	ret z
	cp NUM_POKEMON_INDEXES + 1
	ret nc
	push de
	call EnsureSpeciesMap
	pop de
	ld hl, sRandoMap
	ld d, 0
	add hl, de
	ld e, [hl]
	ret

; e = 0 if this save is a normal game, nonzero if it's a randomizer game.
; Handles sram itself. The answer comes back in e rather than the flags because
; callers reach this through a bank switch.
RandoEnabledFar::
	push hl
	call RandoSramOn
	call RandoEnabled
	ld e, a
	call RandoSramOff
	pop hl
	ret

; Remaps b species bytes starting at hl, stepping c bytes between them.
; Assumes sram is enabled and the randomizer is already known to be on.
RandoRemapBuffer:
.loop
	ld e, [hl]
	push hl
	push bc
	call RandoMapSpecies
	pop bc
	pop hl
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

; Remaps wCurPartySpecies in place, for callers that are holding a data pointer
; and can't spare registers for an argument.
RandoRemapPartySpecies::
	call RandoSramOn
	call RandoEnabled
	jr z, .done
	ld a, [wCurPartySpecies]
	ld e, a
	call RandoMapSpecies
	ld a, e
	ld [wCurPartySpecies], a
.done
	jp RandoSramOff

; Remaps the player's and rival's starters once Oak's Lab has stashed them.
; The rival keeps picking positionally, so whatever STARTER2 became is still
; what he takes if you chose STARTER1.
RandoRemapStarters::
	call RandoSramOn
	call RandoEnabled
	jr z, .done
	ld a, [wCurPartySpecies]
	ld e, a
	call RandoMapSpecies
	ld a, e
	ld [wCurPartySpecies], a
	ld [wPokedexNum], a
	ld a, [wRivalStarterTemp]
	ld e, a
	call RandoMapSpecies
	ld a, e
	ld [wRivalStarterTemp], a
.done
	jp RandoSramOff

; Remaps the wild data ram copies in place, after LoadWildDataCommon has filled
; them. Slot order is preserved so the palette flags in WildPalettePointers stay
; aligned with the species they describe.
; A rate of 0 means that half was never copied, so its buffer is stale and must
; be left alone.
RandoRemapWildData::
	call RandoSramOn
	call RandoEnabled
	jr z, .done
	ld a, [wGrassRate]
	and a
	jr z, .water
	ld hl, wGrassMons + 1
	ld b, NUM_WILDMONS
	ld c, 2
	call RandoRemapBuffer
.water
	ld a, [wWaterRate]
	and a
	jr z, .done
	ld hl, wWaterMons + 1
	ld b, NUM_WILDMONS
	ld c, 2
	call RandoRemapBuffer
.done
	jp RandoSramOff

; Called when a new game starts. Turns the randomizer on for this save if the
; option is set, and off otherwise, so a seed from a previous randomizer game
; can never leak into a normal one.
RandoNewGame::
	ld a, [wOptions3]
	bit BIT_RANDOMIZER, a
	jp z, RandoClearGame
	call RandoSramOn
	; hRandomAdd and hRandomSub are stirred every frame, so this differs between
	; runs. Player entered seeds go through RandoStartGame instead.
	ldh a, [hRandomAdd]
	ld [sRandoSeed], a
	ldh a, [hRandomSub]
	ld [sRandoSeed + 1], a
	call Random
	ld [sRandoSeed + 2], a
	call Random
	ld [sRandoSeed + 3], a
	; an all zero seed would read as "not a randomizer game"
	ld hl, sRandoSeed
	ld a, [hli]
	or [hl]
	inc hl
	or [hl]
	inc hl
	or [hl]
	jr nz, RandoFinishStart
	ld a, 1
	ld [sRandoSeed], a
	jr RandoFinishStart

; Starts a randomizer game using the 4 byte seed at hl.
RandoStartGame::
	call RandoSramOn
	ld de, sRandoSeed
	ld c, 4
	call RandoCopyBytes
	; fall through

; Writes the magic so the state is recognised, and clears sRandoMapSeed so the
; permutation is rebuilt on first use. Assumes sram is on.
RandoFinishStart:
	ld hl, sRandoMagic
	ld a, $52
	ld [hli], a
	ld a, $41
	ld [hli], a
	ld a, $4E
	ld [hli], a
	ld a, $44
	ld [hli], a
	xor a
	ld hl, sRandoMapSeed
	ld c, 4
.clearMapSeed
	ld [hli], a
	dec c
	jr nz, .clearMapSeed
	jp RandoSramOff

; Turns the randomizer off for this save. Called when starting a normal game so
; leftover state from a previous randomizer game is never picked up.
RandoClearGame::
	call RandoSramOn
	xor a
	ld hl, sRandoMagic
	ld c, 8 ; magic and seed
.clear
	ld [hli], a
	dec c
	jr nz, .clear
	jp RandoSramOff

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
	ld hl, sRandoSeed
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
	ld hl, sRandoSeed
	ld de, sRandoRngState
	ld c, 4
	call RandoCopyBytes
	ld hl, sRandoSeed
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
