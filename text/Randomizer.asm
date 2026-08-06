; PureRGBnote: ADDED: randomizer mode text.
; text_start is required after a text_ram_* command before prompt or done.
_RandomizerSeedText::
	text "Randomizer seed"
	line "@"
	text_ram_stringbuffer
	text_start
	prompt

_RandomizerInfoText::
	text "Master switch."
	line "Asks for a seed"
	cont "on a new game."
	para "Leave the seed"
	line "blank to get a"
	cont "random one."
	prompt

; Each row below is read once when a new game starts, so changing one part way
; through a run has no effect on that run.
_RandomizerWildInfoText::
	text "Shuffles what"
	line "lives in grass"
	cont "and water, plus"
	cont "gifts, statics"
	cont "and trades."
	prompt

_RandomizerTrainersInfoText::
	text "Shuffles the"
	line "teams trainers"
	cont "use. Kept apart"
	cont "from the wild"
	cont "shuffle."
	prompt

_RandomizerStartersInfoText::
	text "Shuffles the"
	line "three #MON on"
	cont "OAK's table."
	prompt

_RandomizerTmsInfoText::
	text "Shuffles which"
	line "TM you are"
	cont "given. What each"
	cont "TM teaches does"
	cont "not change."
	prompt

_RandomizerItemsInfoText::
	text "Shuffles items"
	line "found on the"
	cont "ground or hidden."
	para "Key items stay"
	line "where they are."
	prompt

; Generic replacement for the starter prompts, which name a fixed species and type.
_OaksLabYouWantRandomizedText::
	text "So! You want"
	line "this #MON,"
	cont "@"
	text_ram_namebuffer
	text "?"
	done
