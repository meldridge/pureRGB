; PureRGBnote: ADDED: randomizer mode text.
; text_start is required after a text_ram_* command before prompt or done.
_RandomizerSeedText::
	text "Randomizer seed"
	line "@"
	text_ram_stringbuffer
	text_start
	prompt

_RandomizerInfoText::
	text "If ON, you'll be"
	line "asked for a seed"
	cont "when you start a"
	cont "new game."
	para "Leave it blank"
	line "for a random one."
	prompt

; The rows below are read once when a new game starts, so changing one part way
; through a run has no effect on that run.
_RandomizerWildInfoText::
	text "If ON, shuffles"
	line "wild #MON, and"
	cont "any you're given"
	cont "or trade for."
	prompt

_RandomizerTrainersInfoText::
	text "If ON, shuffles"
	line "the #MON that"
	cont "<TRAINER>s use."
	prompt

_RandomizerStartersInfoText::
	text "If ON, shuffles"
	line "the three #MON"
	cont "on OAK's table."
	prompt

_RandomizerTmsInfoText::
	text "If ON, shuffles"
	line "which TM you get,"
	cont "but not what each"
	cont "TM teaches."
	prompt

_RandomizerItemsInfoText::
	text "If ON, shuffles"
	line "items you find"
	cont "or uncover. Key"
	cont "items stay put."
	prompt

; Generic replacement for the starter prompts, which name a fixed species and type.
_OaksLabYouWantRandomizedText::
	text "So! You want"
	line "this #MON,"
	cont "@"
	text_ram_namebuffer
	text "?"
	done
