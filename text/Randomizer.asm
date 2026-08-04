; PureRGBnote: ADDED: randomizer mode text.
; Named the mon you're actually being given, since the vanilla starter prompts
; state a species and a type that are both wrong once starters are shuffled.
_RandomizerSeedText::
	text "Randomizer seed"
	line "@"
	text_ram_stringbuffer
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

_OaksLabYouWantRandomizedText::
	text "So! You want"
	line "this #MON,"
	cont "@"
	text_ram_namebuffer
	text_start
	text "?"
	done
