# How the pictures of the car were made

`public/cockpit.webp` and the house fronts in `public/facades/` are made up pictures. They show no real
brands, people or places. The product owner has not picked them yet, so they may be replaced.

| Picture | Size and quality | References |
| --- | --- | --- |
| Cockpit | 1536 x 1024, high | `data/design/images/spark/interior.jpg`, then the previous picture |
| Five facades (`ochre`, `red`, `cream`, `cafe`, `salmon`) | 1024 x 1024, medium, saved at 768 x 768 | None |

All were made with `ailloy image` and the model gpt-image-2.5-flare on Microsoft Foundry. They cost 0.32 USD
in all.

The prompts asked for a windscreen in one flat green. The green was keyed out by its color, so the windscreen
is transparent, and the picture was saved as WebP. The facades are the fronts of four storey houses in the
warm colors of the Spark's pictures.

Where the windscreen, the mirror and the two displays are on the cockpit picture is in `src/layout.ts`. A new
picture needs new numbers there, and new pivots for the wipers in `src/scene/glass.ts`.

## The first prompt for the cockpit

> Photorealistic interior photograph from the driver's seat of the same small electric city car as
> in the reference image, seen straight ahead from the driver's eye height, camera centered slightly
> right of the steering wheel, 24mm lens, real light, accurate materials, no CGI look. Left-hand
> drive. Light grey textile and a pale wood strip across the dashboard, round side air vents with
> white rings. In front of the driver a simple two-spoke steering wheel with a small glowing chevron
> V emblem in the hub, and behind it a wide low driver display that is switched off: a completely
> plain, flat, dark charcoal glass panel with nothing on it. In the middle of the dashboard a large
> landscape tablet-like touchscreen on a stand, about one third of the image width, also switched
> off: a completely plain, flat, dark charcoal glass panel with nothing on it, facing the camera
> straight on, with square undistorted corners. The whole dashboard fills the lower 48 percent of
> the picture. The complete windscreen and the side window openings above the dashboard are one
> perfectly flat, uniform, pure bright green (RGB 0,255,0) area with no reflections, no gradients,
> no scenery, no rain, like a chroma key screen, bounded by the light grey A pillars and the
> headliner with sun visors; a small rear view mirror hangs at the top center, its glass plain dark
> grey. No people, no hands, no text, no letters, no numbers, no icons, no logos other than the V
> emblem, no watermark.

## The second prompt, with the previous picture as the reference

> The same car interior as in the reference image, as a photorealistic photograph taken from between
> the two front seats at the height of the driver's eyes, looking straight ahead. Only the
> dashboard, steering wheel and windscreen are visible: no seats, no headrests, no people, no hands.
> The windscreen fills the upper 55 percent of the picture and is one perfectly flat, uniform, pure
> bright green area (RGB 0,255,0), like a chroma key screen: no reflections, no gradients, no
> scenery. Slim light grey A pillars at the far left and right, a thin strip of light grey headliner
> and a small dark rear view mirror at the top center. The dashboard fills the lower 45 percent,
> seen straight on: pale wood strip, dark textured top, round vents with white rings at both ends,
> the small two-spoke steering wheel with the glowing V emblem to the left, and behind it a wide low
> driver display that is switched off, plain flat dark charcoal glass. In the middle a large
> landscape touchscreen, 38 percent of the picture width, switched off: plain flat dark charcoal
> glass, perfectly rectangular, seen exactly straight on, thin black frame, standing freely in front
> of the dashboard, its top edge reaching up into the lower part of the windscreen. Real soft
> daylight, accurate materials. No text, no letters, no numbers, no icons, no other logos, no
> watermark.

## Drawn by code

Everything else on the street is drawn by code when the car starts: the stones, the leaves, the skyline, the
cars, the lamps and the signs.
