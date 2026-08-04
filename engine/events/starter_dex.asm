; this function temporarily makes the starters owned so that the full Pokedex information gets displayed in Oak's lab
; PureRGBnote: CHANGED: refactored to mark the starters indicated by the starter constants
; by converting them to dex constants so no changes are necessary other than changing the constant values
; downside is this function takes up more space than before
; PureRGBnote: CHANGED: also marks the mon actually being shown. In randomizer mode
; that is not one of the three starter constants, and an unowned mon takes a
; branch in ShowPokedexData that never watches for a button, so the page hangs.
StarterDex::
	ld a, [wPokedexNum] ; the mon to display, saved because marking overwrites it
	ld d, a
	ld b, FLAG_SET
	call .markStarters
	push de
	ld a, d
	ld [wPokedexNum], a
	callfar ShowPokedexData
	pop de
	ld b, FLAG_RESET
	call .markStarters
	ld a, d
	ld [wPokedexNum], a
	ret

; b = FLAG_SET or FLAG_RESET, d = the mon being displayed
.markStarters
	ld a, d
	call .markOne
	ld hl, StarterDexArray
	ld c, 3 ; number of starters available
.loop
	ld a, [hli]
	push hl
	push bc
	call .markOne
	pop bc
	pop hl
	dec c
	jr nz, .loop
	ret

; a = pokemon index constant, b = flag action
.markOne
	ld [wPokedexNum], a
	push de
	push bc
	call IndexToPokedex
	pop bc
	ld a, [wPokedexNum]
	dec a
	ld c, a
	ld hl, wPokedexOwned
	call FlagAction
	pop de
	ret

StarterDexArray:
	db STARTER1
	db STARTER2
	db STARTER3
