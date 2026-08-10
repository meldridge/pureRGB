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

_OaksLabYouWantRandomizedText::
	text "So! You want"
	line "this #MON,"
	cont "@"
	text_ram_namebuffer
	text "?"
	done
