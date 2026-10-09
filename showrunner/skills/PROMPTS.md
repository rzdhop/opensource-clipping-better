## Writing the prompts (you write every one; the server only makes the picture or the clip)

You write each prompt in full and send it as `values.prompt` of `comfy_submit` (Rida does not need to read prompts;
show one only if he asks). The story's files are your material: `01-universe.md` `## Medium`, each `sheet.md` `## Head` and
`## Voice (en)`, each `03-places/<place>/plate.md` `## Setting`, the shot's lines. Write the prompt into the shot
too (`keyframe_prompt`, `clip_prompt` in `shots.json`) so the story keeps what was sent; the job journal keeps it as
well. The prompts are in English; only the quoted lines are in the story's language.

**What has worked (chosen by Rida 2026-10-08).** The patterns below are the prompts Rida preferred (take
s33 for one speaker, s22 for two or three), with one change since: the closing sentence. Keep their order and their
fixed sentences; change only what comes from this story's files. Departing from a pattern is fine when the story
needs it — note in the shot what you changed and why, so a good result can be kept.

**Every prompt**

- Opens with the universe's `## Medium` word for word, then each character's `## Head` word for word (+ ".").
- Never names what must not appear: no "subtitles", "captions", "text", "no people", "nobody else in the room"
  (the models ignore the negative prompt and draw what they read). Ask for what you want instead.
- A clip prompt ends with exactly: `One continuous, clean cinematic shot from the first frame to the last.`
- A keyframe or cast image names nobody but the characters in it: never "someone off-screen", "a person beside
  the camera", "another" (A-222: "turned toward someone just off-screen" drew a stray human).

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

**Keyframe** (704×1280; `t2i_flux2_klein`, or `edit_flux2_klein_multiref` with the locked full bodies of the
characters in frame as references)

```
<Medium> <Head of each character in frame>. Setting: <Setting>. Medium close shot from the waist up, <framing>,
mouth closed, <expression>, soft cinematic light, vertical 9:16 framing, <who>.
```

- `<framing>`, one character: `<Name> faces the camera` (the default). The three-quarter wording
  `<Name> with the head turned three-quarters to the right, eyes looking past the right edge of the frame` only once
  it has passed its check on this story. Two or three characters: `<A> and <B> stand close together, facing each
  other mid-conversation` (three: `<A>, <B> and <C> stand …`).
- `<who>`: `only this one character, nobody else` · `only these two characters, nobody else` · `only these three
  characters, nobody else`.
- `<expression>`: a few words of the face (default `a tense and composed expression`).

**Clip, one speaker** (`ltx25_i2v_speech`, 5 s; the s33 pattern)

```
Use the provided start image as the first frame. <Medium> <Head>. Setting: <Setting>. <Name> talks to someone just
off-screen beside the camera, in three-quarter view, the eyeline passing just past the lens and never looking into
it, as in a conversation scene of a drama, and says in <French|English>, with the voice of <Voice (en)>: "<line>"
The mouth moves naturally with every word, a small head tilt, a breath before and a beat of silence after the line.
Medium close-up, the camera holds still on the speaker, soft natural motion only. Audio: the clear voice close to
the microphone, quiet room tone, no music. One continuous, clean cinematic shot from the first frame to the last.
```

**Clip, two or three characters** (`ltx25_i2v_speech`, 10 s; the s22 pattern — multi-speaker clips preferred)

```
Use the provided start image as the first frame. <Medium> <Head A>. <Head B>. Setting: <Setting>. They speak in turn,
each one's mouth moving only on their own line, the other listening and reacting: <A> says in <language>, with the
voice of <Voice A>: "<line>" <B> says in <language>, with the voice of <Voice B>: "<line>" Medium two-shot, the
camera holds still. Audio: two distinct voices close to the microphone, quiet room tone, no music. One continuous,
clean cinematic shot from the first frame to the last.
```

**Clip, silent reaction** (`ltx25_i2v_speech`, 5 s)

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
in order; each speaker's voice is their sheet's `## Voice (en)`; the closing sentence is there (clips); nothing
unwanted is named; the size and seconds match the template; the batch is inside the cost you announced.
