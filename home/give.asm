GiveItem::
; Give player quantity c of item b,
; and copy the item's name to wStringBuffer.
; Return carry on success.
;;;;;;;;;; PureRGBnote: ADDED: randomizer mode. Tms are swapped here rather than
; at each place one is handed out, so the name copied below is the one that
; actually arrives. Range checked first, so an ordinary item pays nothing.
; bc is saved across the call because the bank switch clobbers it, and c is the
; quantity.
	ld a, b
	sub TM01
	cp NUM_TMS
	jr nc, .notATm
	ld e, a
	push bc
	callfar RandoRemapTm
	pop bc
	ld a, e
	add TM01
	ld b, a
.notATm
;;;;;;;;;;
	ld a, b
	ld [wNamedObjectIndex], a
	ld [wCurItem], a
	ld a, c
	ld [wItemQuantity], a
	ld hl, wNumBagItems
	call AddItemToInventory
	ret nc
	call GetItemName
	call CopyToStringBuffer
	scf
	ret

; PureRGBnote: ADDED: when giving a pokemon we can choose what pokeball it is in and whether it is alternate palette by changing a
GivePokemon::
; Give the player monster b at level c.
	xor a
GivePokemonCommon::
	ld [wIsAltPalettePkmnData], a
	ld a, b
	ld [wCurPartySpecies], a
	ld a, c
	ld [wCurEnemyLevel], a
	xor a ; PLAYER_PARTY_DATA
	ld [wMonDataLocation], a
	farjp _GivePokemon
