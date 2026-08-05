SECTION "Sprite Buffers", SRAM

sSpriteBuffer0:: ds SPRITEBUFFERSIZE
sSpriteBuffer1:: ds SPRITEBUFFERSIZE
sSpriteBuffer2:: ds SPRITEBUFFERSIZE

	ds 206 ; used to be $100 = 256

; PureRGBnote: ADDED: byte array of whether each pokemon in current hall of fame team data should use an alt color palette
; only uses bits 0-5 in each byte, since the party size is 6.
sHallOfFamePalettes:: ds HOF_TEAM_CAPACITY 

; hall of fame data, contains species, level, and nickname of each pokemon for each team
sHallOfFame:: ds HOF_TEAM * HOF_TEAM_CAPACITY

sCustomBallNames:: ds NAME_LENGTH * NUM_CUSTOM_BALLS ; 176

	ds 256 ; extra space in case more custom balls are added later

; used to restore music to previous point after battle
sAudioRamBackup:: ds wAudioWRAMEnd - wAudioWRAMStart

	ds 100 ; extra space in case audio wram increases in size later

sBoxRenamedFlags:: flag_array NUM_BOXES ; 2 bytes
sBoxRenamedFlagsEnd::
sBoxNames:: ds NUM_BOXES * BOX_NAME_LENGTH ; 72 bytes

; PureRGBnote: ADDED: randomizer state.
; Must stay outside sGameData: growing that block moves sGameDataEnd and every
; existing save then fails its checksum. It can't live in wram either, since
; bank 1 is full and the game never writes SVBK.
; sRandoMagic marks the block as ours, as nothing here is checksummed.
; sRandoMapSeed is the seed sRandoMap was built for; a mismatch rebuilds it.
sRandoMagic:: ds 4
sRandoSeed:: ds 4
sRandoMapSeed:: ds 4
; the permutation itself: sRandoShuffle[n] replaces the species at pool position
; n. RandoPoolPos turns a species into that position, so no second table keyed by
; species is needed.
sRandoShuffle:: ds RANDO_POOL_SIZE
sRandoRngState:: ds 4
sRandoRngTemp:: ds 4
sRandoLo:: db
sRandoI:: db
sRandoJ:: db
sRandoHmMask:: db
sRandoAnchorNext:: db
sRandoPI:: db
sRandoPJ:: db

; still quite a bit of space left here, around 1000 bytes



SECTION "Save Data", SRAM

	ds $598

sGameData::
sPlayerName::  ds NAME_LENGTH
UNION
sMainData::    ds wMainDataEnd - wMainDataStart
NEXTU
ds wSpriteOptions - wMainDataStart
sSpriteOptions:: db
sSpriteOptions2:: db
sOptions2:: db
sSpriteOptions3:: db
sSpriteOptions4:: db
sOptions3::db
NEXTU
ds wOptions - wMainDataStart
sOptions::db
NEXTU
ds wWorldOptions - wMainDataStart
sWorldOptions::db
sOptions4::db
NEXTU
ds wSpriteOptions5 - wMainDataStart
sSpriteOptions5::db
ENDU

sSpriteData::  ds wSpriteDataEnd - wSpriteDataStart
sPartyData::   ds wPartyDataEnd - wPartyDataStart
sCurBoxData::  ds wBoxDataEnd - wBoxDataStart
sTileAnimations:: db
sGameDataEnd::
sMainDataCheckSum:: db


; The PC boxes will not fit into one SRAM bank,
; so they use multiple SECTIONs
DEF box_n = 0
MACRO boxes
	REPT \1
		DEF box_n += 1
	sBox{d:box_n}:: ds wBoxDataEnd - wBoxDataStart
	ENDR
ENDM

SECTION "Saved Boxes 1", SRAM

; sBox1 - sBox6
	boxes 6
sBank2AllBoxesChecksum:: db
sBank2IndividualBoxChecksums:: ds 6

SECTION "Saved Boxes 2", SRAM

; sBox7 - sBox12
	boxes 6
sBank3AllBoxesChecksum:: db
sBank3IndividualBoxChecksums:: ds 6

; All 12 boxes fit within 2 SRAM banks
	ASSERT box_n == NUM_BOXES, \
		"boxes: Expected {d:NUM_BOXES} total boxes, got {d:box_n}"

ENDSECTION
