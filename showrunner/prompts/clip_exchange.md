<!--
Two or three characters in frame, speaking in turn (multi-speaker clips preferred, D7).
GOLDEN: the batch-a exchange prompt Rida preferred (take s22, 2 and 3 speakers), word for word except
the closing {{clean_frame}}. "Medium two-shot" and "two distinct voices" are kept for three speakers
too, because the golden has them: changing them is a decision for Rida.
Placeholders: medium, heads (each sheet ## Head + ".", joined by spaces), setting, turns (one
turn.md per line, in order, joined by spaces), clean_frame.
-->
Use the provided start image as the first frame. {{medium}} {{heads}} Setting: {{setting}}.
They speak in turn, each one's mouth moving only on their own line, the other listening and reacting: {{turns}}
Medium two-shot, the camera holds still.
Audio: two distinct voices close to the microphone, quiet room tone, no music. {{clean_frame}}
