## Writing the prompts (you write every one; the server only makes the picture or the clip)

You write each prompt in full and send it as `values.prompt` of `comfy_submit` (Rida does not need to read prompts;
show one only if he asks). The story's files are your material: `01-universe.md` `## Medium` and `## Extras`, each
`sheet.md` `## Head` and `## Voice (en)`, each `03-places/<place>/plate.md` `## Setting` and `## Extras`, the shot's
lines with how each is said. Write the prompt into the shot too (`keyframe_prompt`, `clip_prompt` in `shots.json`)
so the story keeps what was sent; the job journal keeps it as well. The prompts are in English; only the quoted
lines and the spelled-out signs are in the story's language.

**What has worked (chosen by Rida 2026-10-08, upgraded 2026-10-09).** The patterns below are the prompts Rida
preferred (take s33 for one speaker, s22 for two or three), with the closing sentence changed since, and the
upgrade Rida asked for after the first real episode: busy sets, background people, strong emotions, more characters
per clip. Keep their order and their fixed sentences; change only what comes from this story's files. Departing
from a pattern is fine when the story needs it — note in the shot what you changed and why, so a good result can
be kept.

**Every prompt**

- Opens with the universe's `## Medium` word for word, then each character's `## Head` word for word (+ ".").
- Never names what must not appear: no "subtitles", "captions", "no people", "nobody else in the room" (the models
  ignore the negative prompt and draw what they read). Ask for what you want instead.
- A clip prompt ends with exactly: `One continuous, clean cinematic shot from the first frame to the last.`
- A keyframe or cast image names nobody it does not show: never "someone off-screen", "a person beside the camera",
  "another" (A-222: "turned toward someone just off-screen" drew a stray human). Background people are named one by
  one (below), never as "people", "a crowd" or "coworkers" alone: those words drew human faces in 4 pictures of 6.

**The look that holds attention (every keyframe, every universe)**

- **A full set.** The place's `## Setting` gives 6–10 concrete things at three depths (foreground, middle, deep
  background), a light source and the time of day. A plain wall or an empty room is never the background.
- **Background people.** From the place's `## Extras` (drawn from the universe's `## Extras`), 3–6 of them, each named
  with its own head and clothes, all of them smaller and softly out of focus behind the action, doing one silent thing
  (staring, walking past, sipping, typing). In a fruit universe, word for word in the pattern: `every one of them with
  a whole fruit for a head and cartoon eyes drawn on the fruit's skin: a yellow banana-headed man in a grey sweater,
  an orange-headed woman in a beige blouse, …`. An extra never shares a main character's head, colour or outfit;
  their clothes stay neutral (grey, beige, white, denim) so the cast pops.
- **A strong emotion and a physical action.** Every keyframe gives each character in frame a clear emotion on the face
  (furious, shocked, crying, smug, terrified) and one action of the body (a hand slammed on a table, fists clenched,
  a folder hugged to the chest, a phone held up). Big but believable: a telenovela, not a cartoon gag.
- **Readable text, short.** A sign or a label may be spelled out in capitals when it adds to the scene (`a pink neon
  sign reading CAFÉ`, `a red folder stamped CONFIDENTIEL in big white letters`): one or two words, on an object or a
  sign, never a sentence. Text far in the background does not matter.
- **Left to right.** With two or three characters, the prompt places them: `<A> on the left and <B> on the right`.
  The assembly punches in on each speaker from that order, so keep `characters` in `shots.json` in the same order
  (or set `positions` when the picture came out the other way).

**Cast: the full body** (`t2i_flux2_klein`, 832×1216, 3–5 seeds)

```
<Medium> <Head>. A full-body character reference: the whole figure from head to feet, standing upright in a relaxed
neutral pose, arms loose at the sides, facing the camera, centred on a plain neutral light-grey background, flat even
studio light, only this one character, nobody else.
```

**Cast: turnaround and expressions** (`edit_flux2_klein_multiref`, 832×1216, the locked `full_body.png` in
`ref1`–`ref4`)

```
<Medium> A character turnaround sheet of <Head>. Four full-body views side by side on a plain light-grey background,
flat even studio light: front view, three-quarter view, side view, back view. The same character in every view,
same colours, same outfit and same proportions as the reference image.
```

```
<Medium> An expression sheet of <Head>: six head-and-shoulders portraits in a 3x2 grid on a plain light-grey
background, the same lighting and the same framing in each: joy, anger, doubt, love, thinking, sadness. The same
character in every portrait, same colours and same outfit as the reference image.
```

**Keyframe** (704×1280; `edit_flux2_klein_multiref` with the locked full bodies of the characters in frame as
references, in the order of `characters`, repeated to fill `ref1`–`ref4`)

```
<Medium> <Head of each character in frame>. Setting: <Setting>. In the foreground, <framing>, mouths closed;
<emotion and action of each character>. <Extras sentence>. Soft cinematic light with rich depth, vibrant colours,
vertical 9:16 framing, the main characters sharp and large in the foreground.
```

- `<framing>`, two characters: `A medium close shot from the waist up, the two fruit heads large in the frame, <A> on
  the left and <B> on the right, close together, facing each other mid-confrontation` (three: `<A> on the left, <B>
  in the middle and <C> on the right`). Use the universe's word for the heads ("fruit heads" in a fruit story).
- `<framing>`, one character: `a medium close-up from the waist up, the head and the shoulders large in the upper
  half of the vertical frame, <Name> faces the camera` (the default; the full body as reference otherwise draws a
  full-length figure with a small head). The three-quarter wording `<Name> with the head turned three-quarters to the
  right, eyes looking past the right edge of the frame` only once it has passed its check on this story.
- `<Extras sentence>`: `In the background, smaller and softly out of focus, <N> more <universe> people, every one of
  them with <the universe's head rule>: <extra 1>, <extra 2>, …, <what they do>` (see above).
- A quiet, intimate shot may leave the extras out: then end with `only these two characters, nobody else` (or `only
  this one character, nobody else`) instead of the last sentence.

**Clip, one speaker** (`ltx25_i2v_speech`, 5 s; the s33 pattern)

```
Use the provided start image as the first frame. <Medium> <Head>. Setting: <Setting>. <Name> talks to someone just
off-screen beside the camera, in three-quarter view, the eyeline passing just past the lens and never looking into
it, as in a conversation scene of a drama, and says in <French|English>, <how it is said>, with the voice of
<Voice (en)>: "<line>" The mouth moves naturally with every word, a small head tilt, a breath before and a beat of
silence after the line. <The action: a tear rolls down, a fist hits the table…> <Extras action, if any: behind,
out of focus, the coworkers keep walking past in silence.> Medium close-up, the camera holds still on the speaker,
soft natural motion only. Audio: the clear voice close to the microphone, quiet room tone, no music. One continuous,
clean cinematic shot from the first frame to the last.
```

**Clip, two or three characters** (`ltx25_i2v_speech`, 10 s; the s22 pattern — the default: most clips have two or
three characters and three short lines)

```
Use the provided start image as the first frame. <Medium> <Head A>. <Head B>. Setting: <Setting>. They speak in turn,
each one's mouth moving only on their own line, the other listening and reacting: <A> says in <language>, <how it is
said>, with the voice of <Voice A>: "<line>" <B> says in <language>, <how it is said>, with the voice of <Voice B>:
"<line>" <A> says in <language>, <how it is said>, with the voice of <Voice A>: "<line>" <Extras action: behind
them, out of focus, the coworkers freeze, turn their heads and stare in silence.> Medium two-shot, the camera holds
still. Audio: two distinct voices close to the microphone, quiet room tone, no music. One continuous, clean
cinematic shot from the first frame to the last.
```

- `<how it is said>`: the script's delivery in a few words — `shouting, shocked and furious, her voice shaking`,
  `icy and calm, almost whispering`, `through gritted teeth, furious and low`, `her voice trembling and small, close
  to tears`. It changes the face and the body as much as the voice: it is where the emotion comes from.
- When a sheet's voice carries a mood that fights the line (a "smiling", "playful" voice on a sad line), keep only its
  age and timbre words in the prompt (`the voice of a bright young woman, here small and broken`): the mood word draws
  a smile on the face. The locked voice is laid on afterwards anyway.
- Extras stay silent and keep their action small; never give them a line or a mouth movement.

**Clip, silent reaction** (`ltx25_i2v_speech`, 5 s — rare: a scene plays better with someone talking)

```
Use the provided start image as the first frame. <Medium> <Head>. Setting: <Setting>. <Name> listens to someone just
off-screen beside the camera, in three-quarter view, the eyeline passing just past the lens, and reacts in silence
with the eyes and brows: <what the face shows>; lips closed, a small breath, soft natural motion only. Medium
close-up, the camera holds still on the listener. Audio: quiet room tone only. One continuous, clean cinematic shot
from the first frame to the last.
```

**The speech budget.** About 2.4 words a second after a beat of silence: a 5 s clip says at most 10 words, a 10 s
clip at most 22 (all its lines together). A line that does not fit is cut in the script, never sped up.

**Before you send, check:** the Medium and every Head are word for word; the lines are the script's exact words,
in order, each with how it is said; each speaker's voice is their sheet's `## Voice (en)` (or its timbre words, see
above); every background person has the universe's head; the characters are placed left to right as in
`characters`; the closing sentence is there (clips); nothing unwanted is named; the size and seconds match the
template; the batch is inside the cost you announced.
