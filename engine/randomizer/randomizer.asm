; PureRGBnote: ADDED: randomizer mode.
; Species are remapped at lookup time instead of rewriting the ROM tables, so a
; single build plays any seed. The permutation lives in sram (wram bank 1 is
; full and the game never switches SVBK) and is rebuilt whenever sRandoMapSeed
; disagrees with sRandoSeed, so callers never have to prime it.
;
; Species are passed in e because rst _Bankswitch clobbers a and bc.

INCLUDE "data/randomizer/species_pool.asm"

DEF RANDO_SEED_LENGTH EQU 8 ; must fit the naming screen's NAME_LENGTH - 1

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
	ld bc, sRandoShuffle
	jr RandoMapThrough

; As above, but through the table used for opposing teams.
RandoMapTrainerSpecies:
	ld bc, sRandoShuffleTrainer
	; fall through

RandoMapThrough:
	ld a, e
	and a
	ret z
	cp NUM_POKEMON_INDEXES + 1
	ret nc
	push bc
	push de
	call EnsureSpeciesMap
	pop de
	pop bc
	ld hl, RandoPoolPos
	ld d, 0
	add hl, de
	ld a, [hl]
	cp RANDO_POOL_SIZE
	ret nc ; outside the pool, so it stands for itself
	ld h, b
	ld l, c
	ld e, a
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
; and can't spare registers for an argument. Uses the opposing team table.
RandoRemapPartySpecies::
	call RandoSramOn
	call RandoEnabled
	jr z, .done
	call RivalStarterSlot
	jr c, .done ; his starter, already substituted
	ld a, [wCurPartySpecies]
	ld e, a
	call RandoMapTrainerSpecies
	ld a, e
	ld [wCurPartySpecies], a
.done
	jp RandoSramOff

; Carries the rival's starter across his battles.
;
; His parties name the species by stage -- Rival1Data holds CHARMANDER,
; Rival2Data CHARMELEON and CHARIZARD -- and those are separate constants that
; would otherwise map independently, so his signature mon would change species
; between fights. The stage cannot come from the trainer class either, since
; SS Anne and Silph Co are both OPP_RIVAL2 at different stages.
;
; So walk the chain the vanilla starter would have taken, and if the species
; being mapped is any of those, substitute the matching stage of the chain his
; actual starter takes. Carry set if it was substituted.
RivalStarterSlot:
	ld a, [wCurOpponent]
	cp OPP_RIVAL1
	jr z, .isRival
	cp OPP_RIVAL2
	jr z, .isRival
	cp OPP_RIVAL3
	jr nz, .no
.isRival
	ld a, [wRivalStarter] ; the positional constant, not the species
	ld d, a
	call RandoStarterSpecies
	ld e, a ; his actual starter
	ld a, [wCurPartySpecies]
	ld c, a
	ld b, 3 ; base, first evolution, second
.walk
	ld a, d
	cp c
	jr z, .matched
	ld a, d
	call RandoEvolutionOf
	ld d, a
	ld a, e
	call RandoEvolutionOf
	ld e, a
	dec b
	jr nz, .walk
.no
	and a
	ret
.matched
	ld a, e
	ld [wCurPartySpecies], a
	scf
	ret

; a = species, returns a = what it evolves into, or itself if it does not.
RandoEvolutionOf:
	push hl
	push de
	ld hl, RandoEvolvesTo
	ld d, 0
	ld e, a
	add hl, de
	ld a, [hl]
	and a
	jr nz, .evolves
	ld a, e ; nothing to evolve into, so stay put
.evolves
	pop de
	pop hl
	ret

; Remaps wCurPartySpecies for a gift. Anything handed over goes through the wild
; table, so a gift is something you could have caught.
; The Game Corner reads its prize level by species before getting here, so the
; level stays the one the slot is meant to hand out.
RandoRemapGift::
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

; Remaps both halves of an in-game trade. What you must hand over moves with the
; wild table too, so it stays something this seed lets you catch.
RandoRemapTrade::
	call RandoSramOn
	call RandoEnabled
	jr z, .done
	ld a, [wInGameTradeGiveMonSpecies]
	ld e, a
	call RandoMapSpecies
	ld a, e
	ld [wInGameTradeGiveMonSpecies], a
	ld a, [wInGameTradeReceiveMonSpecies]
	ld e, a
	call RandoMapSpecies
	ld a, e
	ld [wInGameTradeReceiveMonSpecies], a
.done
	jp RandoSramOff

; Remaps wNamedObjectIndex, for text naming a starter. The starter variables
; hold the original constants, so the name has to be mapped before printing.
RandoRemapNamedObject::
	call RandoSramOn
	call RandoEnabled
	jr z, .done
	ld a, [wNamedObjectIndex]
	call RandoStarterSpecies
	ld [wNamedObjectIndex], a
.done
	jp RandoSramOff

; As above, but through the wild table, for a name shown for something the player
; is about to be given. The Game Corner draws its prize list straight from ROM,
; so without this the menu advertises a mon the seed will not hand over.
RandoRemapNamedWild::
	call RandoSramOn
	call RandoEnabled
	jr z, .done
	ld a, [wNamedObjectIndex]
	ld e, a
	call RandoMapSpecies
	ld a, e
	ld [wNamedObjectIndex], a
.done
	jp RandoSramOff

; Remaps the starter being shown and given in Oak's Lab.
; wPlayerStarter and wRivalStarter are left holding the original constants:
; StarterToPartyID identifies a rival party by comparing against them, so they
; have to stay positional or every rival battle picks the wrong team.
RandoRemapCurStarter::
	call RandoSramOn
	call RandoEnabled
	jr z, .done
	ld a, [wCurPartySpecies]
	call RandoStarterSpecies
	ld [wCurPartySpecies], a
.done
	jp RandoSramOff

; As above, and mirrors it into wPokedexNum for the ball's dex page.
RandoRemapStarters::
	call RandoRemapCurStarter
	ld a, [wCurPartySpecies]
	ld [wPokedexNum], a
	ret

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
; wStringBuffer is left holding the seed text either way, so what gets shown to
; the player is always something they can type back in.
RandoDeriveSeed::
	call RandoSramOn
	ld a, [wStringBuffer]
	cp '@'
	call z, InventSeedText

; Fold the seed text in by xoring each character into the generator state and
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

; Fills wStringBuffer with a random typable seed. Blank entry goes through the
; same folding as a typed one, so the seed shown back is always re-enterable.
; hRandomAdd is stirred every frame, so this differs between runs.
InventSeedText:
	ld hl, wStringBuffer
	ld c, RANDO_SEED_LENGTH
.nextLetter
	push hl
	push bc
	call Random
	pop bc
	pop hl
.reduce
	cp 26
	jr c, .gotLetter
	sub 26
	jr .reduce
.gotLetter
	add 'A'
	ld [hli], a
	dec c
	jr nz, .nextLetter
	ld [hl], '@'
	ret

; Starts a randomizer game using the 4 byte seed at hl.
RandoStartGame::
	call RandoSramOn
	ld de, sRandoSeed
	ld c, 4
	call RandoCopyBytes
	; fall through

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

; Both tables come from the same seed. The trainer one is built first and set
; aside, then the wild one is built in place from where the generator had got
; to, which is what makes the two disagree. Only the wild table gets the
; guardrail, since it decides what is catchable.
	call ShufflePool
	call ShuffleWithinWindows
	ld hl, sRandoShuffle
	ld de, sRandoShuffleTrainer
	ld c, RANDO_POOL_SIZE
	call RandoCopyBytes

	call ShufflePool
	call ShuffleWithinWindows
	call HmGuardrail
	; fall through

; Copies one entry of RandoStarterTriples into sRandoStarters.
PickStarterSet:
	ld c, RANDO_NUM_STARTER_SETS
	call RandoRandRange
	ld hl, RandoStarterTriples
	ld d, 0
	ld e, a
	add hl, de ; three bytes each, so add the index three times
	add hl, de
	add hl, de
	ld de, sRandoStarters
	ld c, 3
	jp RandoCopyBytes

; a = STARTER1, STARTER2 or STARTER3; returns a = what it is this game.
; Anything else is returned unchanged.
RandoStarterSpecies::
	push bc
	push de
	push hl
	push af
	call EnsureSpeciesMap ; the set is chosen while generating, so make sure it has
	pop af
	ld hl, sRandoStarters
	cp STARTER1
	jr z, .found
	inc hl
	cp STARTER2
	jr z, .found
	inc hl
	cp STARTER3
	jr nz, .notAStarter
.found
	ld a, [hl]
.notAStarter
	pop hl
	pop de
	pop bc
	ret

; Resets sRandoShuffle to the unshuffled pool.
ShufflePool:
	ld hl, RandoPool
	ld de, sRandoShuffle
	ld c, RANDO_POOL_SIZE
	jp RandoCopyBytes

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
	call FindLearnerInWindow
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

; Scans the window of the position in sRandoI for a slot whose species learns the
; move and can trade places with it. Carry set and a = that position if found.
FindLearnerInWindow:
	ld a, [sRandoI]
	ld d, 0
	ld e, a
	ld hl, RandoWindowLo
	add hl, de
	ld a, [hl]
	ld [sRandoLo], a
	ld hl, RandoWindowHi
	add hl, de
	ld b, [hl]
.scan
	ld a, [sRandoLo]
	cp b
	jr z, .check
	jr nc, .notFound
.check
	push bc
	call ImageHasMove
	pop bc
	jr nc, .next
	; the swap still has to respect both windows
	push bc
	ld a, [sRandoLo]
	ld [sRandoJ], a
	call SwapFitsWindows
	pop bc
	jr c, .found
.next
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

; Carry if sRandoI and sRandoJ may exchange occupants.
SwapFitsWindows:
	ld a, [sRandoI]
	call OccupantOrigPos
	ld b, a
	ld a, [sRandoJ]
	call FitsWindow
	ret nc
	ld a, [sRandoJ]
	call OccupantOrigPos
	ld b, a
	ld a, [sRandoI]
	jp FitsWindow

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

DEF RANDO_SHUFFLE_PASSES EQU 2

; Every position offers to trade with a random one inside its window. A trade is
; only taken if both species still sit within range of where they would land, so
; the tolerance holds however many times an entry moves.
ShuffleWithinWindows:
	ld b, RANDO_SHUFFLE_PASSES
.nextPass
	ld c, 0
.nextPosition
	push bc
	ld a, c
	ld [sRandoI], a
	call TryWindowSwap
	pop bc
	inc c
	ld a, c
	cp RANDO_POOL_SIZE
	jr nz, .nextPosition
	dec b
	jr nz, .nextPass
	ret

TryWindowSwap:
	call PickWindowPartner
	ld a, [sRandoI]
	call OccupantOrigPos
	ld [sRandoPI], a
	ld a, [sRandoJ]
	call OccupantOrigPos
	ld [sRandoPJ], a
	ld a, [sRandoPI]
	ld b, a
	ld a, [sRandoJ]
	call FitsWindow
	ret nc
	ld a, [sRandoPJ]
	ld b, a
	ld a, [sRandoI]
	call FitsWindow
	ret nc
	jp SwapShuffleEntries

; Chooses a random position inside the window of sRandoI, into sRandoJ.
PickWindowPartner:
	ld a, [sRandoI]
	ld d, 0
	ld e, a
	ld hl, RandoWindowLo
	add hl, de
	ld a, [hl]
	ld [sRandoLo], a
	ld hl, RandoWindowHi
	add hl, de
	ld a, [hl]
	ld hl, sRandoLo
	sub [hl]
	inc a
	ld c, a
	call RandoRandRange
	ld hl, sRandoLo
	add [hl]
	ld [sRandoJ], a
	ret

; a = position, returns a = the pool position the species there came from
OccupantOrigPos:
	ld hl, sRandoShuffle
	ld d, 0
	ld e, a
	add hl, de
	ld a, [hl]
	ld hl, RandoPoolPos
	ld e, a
	add hl, de
	ld a, [hl]
	ret

; a = target position, b = pool position of the species. Carry if it fits.
FitsWindow:
	ld d, 0
	ld e, a
	ld hl, RandoWindowLo
	add hl, de
	ld a, b
	cp [hl]
	jr c, .no
	ld hl, RandoWindowHi
	add hl, de
	ld a, [hl]
	cp b
	jr c, .no
	scf
	ret
.no
	and a
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
