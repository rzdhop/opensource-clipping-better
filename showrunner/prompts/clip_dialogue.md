<!--
One speaker, LTX-2.5 I2V picture + voice in one pass (plan 36 D7).
GOLDEN: this is the batch-a prompt Rida preferred (take s33), word for word; the only deliberate change
is the closing sentence, now {{clean_frame}} (cfg 1.0 ignores the negative prompt, and naming subtitles
primed burned-in captions). showrunner/tests/test_prompts.py pins it against
docs/plans/36-stage0-batch-a-prompts.json: any other change is a decision, not drift.
Placeholders: medium (01-universe.md ## Medium), head (sheet ## Head, ~70 words, no final period),
setting, framing (prompts.dialogue_framing), language (French|English), voice (sheet ## Voice (en)),
line, clean_frame.
-->
Use the provided start image as the first frame. {{medium}} {{head}}. Setting: {{setting}}.
{{framing}}, and says in {{language}}, with the voice of {{voice}}: "{{line}}"
The mouth moves naturally with every word, a small head tilt, a breath before and a beat of silence after the line.
Medium close-up, the camera holds still on the speaker, soft natural motion only.
Audio: the clear voice close to the microphone, quiet room tone, no music. {{clean_frame}}
