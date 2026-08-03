; PureRGBnote: ADDED: randomizer mode text.
; Named the mon you're actually being given, since the vanilla starter prompts
; state a species and a type that are both wrong once starters are shuffled.
_RandomizerInfoText::
	text "Shuffles wild"
	line "#MON, trainer"
	cont "teams and starters"
	cont "when you start a"
	cont "new game."
	prompt

_OaksLabYouWantRandomizedText::
	text "So! You want"
	line "this #MON,"
	cont "@"
	text_ram_namebuffer
	text_start
	text "?"
	done
