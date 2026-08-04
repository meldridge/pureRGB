; PureRGBnote: ADDED: randomizer mode text.
; text_start is required after a text_ram_* command before prompt or done.
_RandomizerSeedText::
	text "Randomizer seed"
	line "@"
	text_ram_stringbuffer
	text_start
	prompt

_RandomizerInfoText::
	text "Shuffles wild"
	line "#MON, trainers"
	cont "and starters on"
	cont "a new game."
	para "Leave the seed"
	line "blank to get a"
	cont "random one."
	prompt

; Generic replacement for the starter prompts, which name a fixed species and type.
_OaksLabYouWantRandomizedText::
	text "So! You want"
	line "this #MON,"
	cont "@"
	text_ram_namebuffer
	text "?"
	done
