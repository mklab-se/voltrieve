# Runa's avatar

`avatar.jpg` is the avatar of Runa, the Voltrieve assistant (issue #15). It is an emblem, not a
face: Runa is an AI and never looks like a person. Made on 2026-11-17 with the same model and
settings as the car images (`gpt-image-2.5-flare`, quality high, 1024x1024), with
`../fjord/light_detail.jpg` as the style reference. The third of three variants was chosen.

```sh
ailloy image "<prompt below>" -o avatar.png --size 1024x1024 --quality high --variants 3 \
  --ref ../fjord/light_detail.jpg
```

Prompt:

> Photorealistic professional product photograph, shot on a full-frame camera with real light, accurate reflections, no CGI look. A round emblem, the avatar of Runa, the AI assistant of a fictional Scandinavian electric car brand called Voltrieve. In the center, a thin glowing white LED line drawing that is both the brand's small chevron-shaped V emblem and a Norse rune, with one short extra stroke that makes it read as a rune. The light line is set into a smooth disc of deep midnight blue paint with brushed glass, like the car's front panel, with a fine polished silver edge. Soft cool dusk light, subtle reflections, shallow depth of field, dark softly blurred fjord background. Centered, the disc fills most of the square frame, front view. No face, no person, no text, no letters, no logos other than the light mark, no watermarks.

The crops used by the web client (the disc only, 620x620 from x 194, y 172):
`clients/web/public/runa.png` (256 px).
