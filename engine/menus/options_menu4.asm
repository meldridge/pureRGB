; PureRGBnote: ADDED: the randomizer's own page. Every row here is read once
; when a new game starts and then frozen into sram, so changing one part way
; through does nothing to a world already being played.
DEF OPTIONS_PAGE_6_COUNT EQU 6 ; number of options on this page
DEF OPTIONS_PAGE_6_NUMBER EQU 6 ; must be 1 digit.

; format: "bit set" x position, "bit not set" x position, which bit it is, pointer to wram variable
; The category flags are stored inverted -- set means off -- so that a zero byte
; reads as everything on, which is what saves made before they existed hold.
; That is why their two columns are the reverse of RANDOM's.
Options4XPosBitData:
	db 11, 14, BIT_RANDOMIZER
	dw wOptions3
	db 14, 11, FLAG_RANDOM_WILD_OFF % 8
	dw wEventFlags + (FLAG_RANDOM_WILD_OFF / 8)
	db 14, 11, FLAG_RANDOM_TRAINERS_OFF % 8
	dw wEventFlags + (FLAG_RANDOM_TRAINERS_OFF / 8)
	db 14, 11, FLAG_RANDOM_STARTERS_OFF % 8
	dw wEventFlags + (FLAG_RANDOM_STARTERS_OFF / 8)
	db 14, 11, FLAG_RANDOM_TMS_OFF % 8
	dw wEventFlags + (FLAG_RANDOM_TMS_OFF / 8)
	db 14, 11, FLAG_RANDOM_ITEMS_OFF % 8
	dw wEventFlags + (FLAG_RANDOM_ITEMS_OFF / 8)

OptionsMenu4Header:
	dw DrawOptionsMenu4
	dw Options4SetCursorPositionActions
	dw SetOptions4FromCursorPositions
	dw Options4LeftRightFuncs
	dw DisplayOptionMenu ; the last page, so forward wraps to the first
	dw DisplayOptions3
	dw OptionsPageAorSelectButtonDefault
	dw Options4InfoTextJumpTable
	; fall through
DisplayOptions4:
	ld hl, OptionsMenu4Header
	ld bc, OptionsMenu4Data
	jp DisplayOptionMenuCommon

; first byte = y coord
; second byte = which option on the page it is (cancel always = max option value)
Options4CoordOffsetList:
	db 3, 0
	db 5, 1
	db 7, 2
	db 9, 3
	db 11, 4
	db 13, 5
	db PAGE_CONTROLS_Y_COORD, MAX_OPTIONS_PER_PAGE

OptionsMenu4Data:
	db OPTIONS_PAGE_6_COUNT ; length of list
	db OPTIONS_PAGE_6_NUMBER ; current page
	db HOW_MANY_MAIN_OPTIONS_PAGES ; how many pages in total
	dw Options4CoordOffsetList

Options4SetCursorPositionActions:
	dw SetCursorPositionFromOptions4
	dw SetCursorPositionFromOptions4
	dw SetCursorPositionFromOptions4
	dw SetCursorPositionFromOptions4
	dw SetCursorPositionFromOptions4
	dw SetCursorPositionFromOptions4

OptionsMenu4Text:
	db   "RANDOMIZER"
	next " RANDOM:   ON OFF"
	next " WILD:     ON OFF"
	next " TRAINERS: ON OFF"
	next " STARTERS: ON OFF"
	next " TMS:      ON OFF"
	next " ITEMS:    ON OFF@"

DrawOptionsMenu4:
	hlcoord 0, 0
	lb bc, 15, 18
	call TextBoxBorder
	hlcoord 1, 1
	ld de, OptionsMenu4Text
	jp PlaceString

Options4LeftRightFuncs:
	dw Options4CursorToggleFunc
	dw Options4CursorToggleFunc
	dw Options4CursorToggleFunc
	dw Options4CursorToggleFunc
	dw Options4CursorToggleFunc
	dw Options4CursorToggleFunc
	dw CursorCancelRow

; every row on this page sits at the same two columns, and the toggle xors
; between them: 11 and 14 differ by %101.
Options4CursorToggleFunc:
	ld b, %101
	jp GenericOptionsCursorToggleFunc

SetOptions4FromCursorPositions:
	ld de, wOptions1CursorX
	ld hl, Options4XPosBitData
	ld b, OPTIONS_PAGE_6_COUNT
	jp LoopGenericSetOptionsFromCursorPositions

SetCursorPositionFromOptions4:
	ld hl, Options4XPosBitData
	jp SetGenericCursorPositionFromOptions

Options4InfoTextJumpTable:
	dw RandomizerInfoText
	dw RandomizerWildInfoText
	dw RandomizerTrainersInfoText
	dw RandomizerStartersInfoText
	dw RandomizerTmsInfoText
	dw RandomizerItemsInfoText

RandomizerInfoText:
	text_far _RandomizerInfoText
	text_end

RandomizerWildInfoText:
	text_far _RandomizerWildInfoText
	text_end

RandomizerTrainersInfoText:
	text_far _RandomizerTrainersInfoText
	text_end

RandomizerStartersInfoText:
	text_far _RandomizerStartersInfoText
	text_end

RandomizerTmsInfoText:
	text_far _RandomizerTmsInfoText
	text_end

RandomizerItemsInfoText:
	text_far _RandomizerItemsInfoText
	text_end
