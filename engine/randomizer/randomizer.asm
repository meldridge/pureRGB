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

; Remaps the wild data ram copies in place, after LoadWildDataCommon fills them.
; Slot order is kept so the WildPalettePointers flags stay aligned, and a rate of
; 0 means that buffer was never copied and still holds the last map's data.
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
	ld a, NAME_SEED_SCREEN
	ld [wNamingScreenType], a
	callfar DisplayNamingScreen
	; fall through

; Derives the seed from whatever the player typed into wStringBuffer and arms
; the randomizer. An empty buffer means "pick one for me".
RandoSeedFromBuffer::
	call RandoDeriveSeed
	ld hl, RandoSeedText
	rst _PrintText
	ret

; The derivation on its own, with no ui, so it can be tested off-console.
RandoDeriveSeed::
	call RandoSramOn
	ld a, [wStringBuffer]
	cp '@'
	jr z, .rollSeed

; Fold the typed seed in by xoring each character into the generator state and
; stepping it, so nearby seeds land far apart. The starting state has to be
; nonzero, since xorshift can never leave zero.
	ld hl, sRandoRngState
	ld a, $9E
	ld [hli], a
	ld a, $37
	ld [hli], a
	ld a, $79
	ld [hli], a
	ld a, $B9
	ld [hl], a
	ld hl, wStringBuffer
.foldLoop
	ld a, [hli]
	cp '@'
	jr z, .foldDone
	push hl
	ld hl, sRandoRngState
	xor [hl]
	ld [hl], a
	call RandoRand
	pop hl
	jr .foldLoop
.foldDone
	ld hl, sRandoRngState
	ld de, sRandoSeed
	ld c, 4
	call RandoCopyBytes
	jr .checkNotZero

.rollSeed
; hRandomAdd and hRandomSub are stirred every frame, so a blank entry differs
; between runs.
	ldh a, [hRandomAdd]
	ld [sRandoSeed], a
	ldh a, [hRandomSub]
	ld [sRandoSeed + 1], a
	call Random
	ld [sRandoSeed + 2], a
	call Random
	ld [sRandoSeed + 3], a

.checkNotZero
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

; Renders sRandoSeed into wStringBuffer as eight hex digits, most significant
; first, so a rolled seed can be written down and replayed. Assumes sram is on.
RandoSeedToString:
	ld hl, sRandoSeed + 3
	ld de, wStringBuffer
	ld c, 4
.byteLoop
	ld a, [hl]
	swap a
	call .nybble
	ld a, [hl]
	call .nybble
	dec hl
	dec c
	jr nz, .byteLoop
	ld a, '@'
	ld [de], a
	ret
.nybble
	and $0F
	cp 10
	jr c, .digit
	add 'A' - 10
	jr .store
.digit
	add '0'
.store
	ld [de], a
	inc de
	ret

RandoSeedText:
	text_far _RandomizerSeedText
	text_end

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
	call RandoSeedToString
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
	call HmGuardrail

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

; Guarantees at least one species catchable before Surf is needed can learn each
; field move, otherwise a seed can be unwinnable.
HmGuardrail:
	xor a
	ld [sRandoAnchorNext], a
	ld a, 1 ; bit 0 = CUT, 1 = SURF, 2 = STRENGTH
	ld [sRandoHmMask], a
.nextMove
	call AnchorHasMove
	jr c, .satisfied
	call RepairMove
.satisfied
	ld a, [sRandoHmMask]
	add a
	ld [sRandoHmMask], a
	cp 1 << 3
	jr nz, .nextMove
	ret

; carry set if some anchor's replacement learns the move in sRandoHmMask.
AnchorHasMove:
	ld hl, RandoAnchors
	ld b, RANDO_NUM_ANCHORS
.loop
	ld a, [hli]
	push hl
	push bc
	call ImageHasMove
	pop bc
	pop hl
	ret c
	dec b
	jr nz, .loop
	and a ; clear carry
	ret

; a = pool position. Carry set if the species now in that slot learns the move.
ImageHasMove:
	ld hl, sRandoShuffle
	ld d, 0
	ld e, a
	add hl, de
	ld a, [hl]
	ld hl, RandoHmLearners
	ld e, a
	add hl, de
	ld a, [sRandoHmMask]
	and [hl]
	ret z ; no carry
	scf
	ret

; Swaps a learner into an anchor slot, staying inside the anchor's bucket so the
; balance matching still holds. Falls through to the next anchor if that bucket
; has no learner, and each repair spends a different anchor so repairs can't
; undo each other.
RepairMove:
	ld a, RANDO_NUM_ANCHORS
	ld hl, sRandoAnchorNext
	sub [hl]
	ret z ; every anchor already spent
	ld b, a
	ld a, [sRandoAnchorNext]
	ld c, a
	ld hl, RandoAnchors
	ld d, 0
	ld e, a
	add hl, de
.anchorLoop
	ld a, [hli]
	ld [sRandoI], a
	push hl
	push bc
	call FindLearnerInBucket
	pop bc
	pop hl
	jr c, .found
	inc c
	dec b
	jr nz, .anchorLoop
	ret ; no bucket can supply one, leave the seed as it is
.found
	ld [sRandoJ], a
	inc c
	ld a, c
	ld [sRandoAnchorNext], a
	jp SwapShuffleEntries

; Scans the bucket holding the pool position in sRandoI for a slot whose species
; learns the move. Carry set and a = that position if found.
FindLearnerInBucket:
	ld hl, RandoBucketBounds + 1
	ld c, 0
.findBucket
	ld a, [hli]
	ld b, a ; bucket end
	ld a, [sRandoI]
	cp b
	jr c, .gotBucket
	inc c
	jr .findBucket
.gotBucket
	ld hl, RandoBucketBounds
	ld d, 0
	ld e, c
	add hl, de
	ld a, [hl]
	ld [sRandoLo], a
.scan
	ld a, [sRandoLo]
	cp b
	jr nc, .notFound
	push bc
	call ImageHasMove
	pop bc
	jr c, .found
	ld hl, sRandoLo
	inc [hl]
	jr .scan
.found
	ld a, [sRandoLo]
	scf
	ret
.notFound
	and a
	ret

; Swaps sRandoShuffle[sRandoI] and sRandoShuffle[sRandoJ].
SwapShuffleEntries:
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
