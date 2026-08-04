---
name: Landlord Compliance
description: A quiet ledger for UK property tax, worked in a lamp-lit room at night.
colors:
  bg-base: "#100D0A"
  bg-surface: "#191512"
  bg-raised: "#221E1A"
  rule: "#34302B"
  rule-strong: "#4C4741"
  text-muted: "#918B84"
  text-body: "#D5D0CA"
  text-high: "#F4F1ED"
  accent: "#EDB345"
  accent-dim: "#A97E2C"
  accent-ink: "#22190A"
  danger: "#ED695E"
  danger-dim: "#8F3831"
  light-bg-base: "#FAF6F1"
  light-bg-surface: "#FFFDFA"
  light-bg-raised: "#EFEAE4"
  light-rule: "#DED8D1"
  light-rule-strong: "#C0B9B1"
  light-text-muted: "#6E685F"
  light-text-body: "#37322B"
  light-text-high: "#191510"
  light-accent: "#9A6000"
  light-accent-dim: "#D4A96F"
  light-accent-ink: "#FEFBF8"
  light-danger: "#B32322"
  light-danger-dim: "#F2A89E"
typography:
  display:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "30px"
    fontWeight: 300
    lineHeight: 1.15
    letterSpacing: "-0.02em"
  title-lg:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "21px"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "-0.01em"
  title:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 600
    lineHeight: 1.35
    letterSpacing: "normal"
  body:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "0.01em"
  meta:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: "0.01em"
  numeric:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 500
    lineHeight: 1.35
    letterSpacing: "normal"
rounded:
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
  xxl: "48px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "10px 18px"
    height: "38px"
  button-primary-hover:
    backgroundColor: "{colors.accent-dim}"
    textColor: "{colors.accent-ink}"
  button-quiet:
    backgroundColor: "{colors.bg-raised}"
    textColor: "{colors.text-body}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "10px 18px"
    height: "38px"
  review-row:
    backgroundColor: "{colors.bg-base}"
    textColor: "{colors.text-high}"
    typography: "{typography.body}"
    padding: "12px 20px"
  review-row-selected:
    backgroundColor: "{colors.bg-surface}"
    textColor: "{colors.text-high}"
  status-needs-you:
    backgroundColor: "{colors.bg-raised}"
    textColor: "{colors.accent}"
    typography: "{typography.meta}"
    rounded: "{rounded.sm}"
    padding: "2px 8px"
  status-settled:
    backgroundColor: "{colors.bg-raised}"
    textColor: "{colors.text-muted}"
    typography: "{typography.meta}"
    rounded: "{rounded.sm}"
    padding: "2px 8px"
  status-wrong:
    backgroundColor: "{colors.bg-raised}"
    textColor: "{colors.danger}"
    typography: "{typography.meta}"
    rounded: "{rounded.sm}"
    padding: "2px 8px"
  nav-rail:
    backgroundColor: "{colors.bg-surface}"
    textColor: "{colors.text-muted}"
    typography: "{typography.label}"
    width: "220px"
    padding: "16px 12px"
---

# Design

## Overview

A quiet ledger, worked at night.

The scene decides everything: a person at a desk in a home office, lamp on,
room dim, in the fortnight before a quarterly tax deadline. So the theme is
**dark by default**, and the neutrals are **warm** rather than the blue-grey
of every developer tool. The difference between a study with a lamp on and a
server room at 2am is the whole aesthetic position.

That warmth is deliberate route-avoidance, twice over. "UK tax compliance"
reaches first for navy or deep teal, and the seed colour this project
shipped in Task 4 was exactly that. Avoiding it lands you on the second
reflex: terminal-native dark, indistinguishable from every issue tracker
built since 2020. Warm dark is neither.

**The system's one big idea: three status vocabularies are one.** The
database has three (`unclassified/proposed/confirmed/excluded`,
`valid/expiring/expired`, `pending/parsed/failed/categorisation_failed`).
The interface has five states, learned once:

| State | Reads as | transactions | certificates | imports |
|---|---|---|---|---|
| **Working** | neutral, with motion | — | — | pending |
| **Needs you** | accent | unclassified, proposed | expiring | — |
| **Settled** | recedes to muted | confirmed | valid | parsed |
| **Wrong** | danger | — | expired | failed, categorisation_failed |
| **Set aside** | dimmest, struck | excluded | — | — |

Only two of the five carry chroma. Settled work goes quiet, which means a
review queue visibly **drains of colour as it is cleared**. That is the core
loop's reward, built into the palette instead of bolted on as confetti.

**Confidence is deliberately not a colour.** A low-confidence proposal is
not an error, it is "look here first," so confidence drives **sort order and
type weight** and never a tint. Colouring it would collide with the status
system and imply the agent had done something wrong by being unsure.

Layout is dense and rule-based: hairlines and spacing, not cards. Nav rail
on the left (Dashboard, Imports, Review, Certificates), content to the right,
no top bar. Responsive behaviour is structural (the rail collapses to icons
under 900px), never fluid typography.

## Colors

**Strategy: Restrained.** Tinted warm neutrals plus a single accent held
under 10% of any surface. Product register's floor, and correct here: the
screen is mostly money, and money should not be decorated.

Neutrals are tinted toward the brand hue at chroma 0.008–0.012 (OKLCH hue 70
dark, 75 light). There is no `#000` and no `#fff` anywhere in the system,
including the lightest light surface (`#FFFDFA`).

Canonical values are OKLCH; the hex in the frontmatter is the sRGB
round-trip, because Flutter's `Color` is ARGB hex and a split source of
truth would drift.

**Dark (default)**

| Token | OKLCH | Hex | Use |
|---|---|---|---|
| `bg-base` | `0.16 0.008 70` | `#100D0A` | page |
| `bg-surface` | `0.20 0.009 70` | `#191512` | rail, panels, selected row |
| `bg-raised` | `0.24 0.010 70` | `#221E1A` | popovers, pills, inputs |
| `rule` | `0.31 0.010 70` | `#34302B` | hairline between rows |
| `rule-strong` | `0.40 0.012 70` | `#4C4741` | section divisions, input borders |
| `text-muted` | `0.64 0.012 70` | `#918B84` | metadata, settled status, bank text |
| `text-body` | `0.86 0.010 70` | `#D5D0CA` | body |
| `text-high` | `0.96 0.006 70` | `#F4F1ED` | the proposal line, amounts, headings |
| `accent` | `0.80 0.140 80` | `#EDB345` | needs-you, primary action, selection |
| `accent-dim` | `0.62 0.110 80` | `#A97E2C` | accent hover, accent rules |
| `accent-ink` | `0.22 0.030 80` | `#22190A` | text on accent |
| `danger` | `0.68 0.165 27` | `#ED695E` | wrong |
| `danger-dim` | `0.46 0.120 27` | `#8F3831` | danger borders and fills |

Measured contrast on `bg-base`: text-muted 5.76:1, text-body 12.67:1,
text-high 17.28:1, accent 10.25:1, danger 6.26:1; accent-ink on accent
9.16:1. Computed, not eyeballed.

**Light (secondary, not an afterthought)**

Warm paper rather than white. Same roles, `light-` prefixed. The accent
darkens to `#9A6000` so it clears AA on paper (4.80:1); the dark theme's
gold would be 2.1:1 and unreadable.

Measured on `light-bg-base`: text-muted 5.13:1, text-body 11.81:1,
text-high 16.85:1, accent 4.80:1, danger 6.12:1.

**Money is never coloured by sign.** An expense is not an error. Amounts use
`text-high` regardless of direction, with a real minus sign (U+2212, not a
hyphen) and tabular figures. `danger` means something is *wrong*, and
spending money on a roof is not wrong. Most finance UIs get this backwards.

## Typography

**One family: Inter**, at 15px body. Product register does not need a
display/body pairing, and a second family here would be decoration.

**Bundle Inter as an asset; do not use `google_fonts`' runtime fetch.** That
package pulls from Google's CDN on first paint, which is an outbound request
carrying the user's IP on a page displaying their bank transactions. This
project already disables CrewAI telemetry for exactly that reason; fetching
a font is the same class of leak with a friendlier name.

It is not free: four static weights measure **782 KB gzipped** in a real
`flutter build web`, and Flutter preloads all four even on the sign-in
screen. Subsetting to Latin would cut most of that. Keep it in proportion
though: `canvaskit.wasm` is **5.6 MB**, so the font is not where the weight
conversation lives.

**Measured 2026-08-04, then fixed, then re-measured.** Bundling Inter removed
*a* Google request, not *the* Google requests. A built web app was also
fetching `canvaskit.wasm` (5.6 MB) and `canvaskit.js` from `www.gstatic.com`
and a Roboto `woff2` from `fonts.gstatic.com` — on a page showing bank
transactions.

CanvasKit is now served from our own origin via `web/flutter_bootstrap.js`
(`config.canvasKitBaseUrl`). Neither `--dart-define=UseLocalCanvasKit=true`
nor `--dart-define=FLUTTER_WEB_CANVASKIT_URL=...` works: the runtime
bootstrap re-derives the URL from `engineRevision`, so only the runtime
config key does. Verified in a browser, not from flag documentation.

The Roboto fallback is now self-hosted too, via the same bootstrap's
`fontFallbackBaseUrl`. Flutter's engine downloads a Roboto face eagerly even
though nothing on screen uses one, so the file is vendored at the exact path
the engine appends — `fallback-fonts/roboto/v32/KFOmCnqEu92Fr1Me4GZLCzYlKw.woff2`,
which is the SDK's own `Roboto-Regular.ttf` under a `.woff2` name. Skia sniffs
the container, so the extension is cosmetic; that was proved with
`CanvasKit.Typeface.MakeFreeTypeFaceFromData`, not assumed.

**The app now makes zero external requests.** Measured 2026-08-04 on a real
`flutter build web` served over HTTP: 15 resources, all same-origin,
`external: []`.

```js
const here = location.origin;
performance.getEntriesByType('resource').map(e => e.name)
  .filter(n => !n.startsWith(here) && !n.startsWith('data:') && !n.startsWith('blob:'));
```

**Grepping the build output is not this check** and must not be substituted
for it. `build/web` contains thirty-odd absolute URLs, nearly all licence
text in `NOTICES`, plus `www.gstatic.com` sitting in `flutter.js` as the
branch our config short-circuits. A grep says "fail" on a clean build. Only
a browser can tell a string in a bundle from a request on the wire.

A trap worth knowing: Flutter registers a service worker that caches the
whole build. Three consecutive "the fix didn't work" measurements were the
service worker serving the first build. Unregister it and clear
`flutter-app-cache` before believing any before/after network measurement.

## Elevation

**Dark uses light, not shadow.** Depth comes from surface lightness steps
(`bg-base` → `bg-surface` → `bg-raised`) plus a `rule` hairline. Drop shadows
on a near-black surface produce grey haze, not depth. Flutter's `surfaceTint`
elevation overlay is disabled; the steps above are the whole vocabulary.

Light gets exactly one shadow, on genuinely floating things (the category
popover, a menu): `0 8px 24px rgba(25, 21, 16, 0.10)`. Nothing else in the
system casts one. No glassmorphism, no blur.

**No cards.** Rows are separated by a 1px `rule` and spacing. A card here
would wrap content that has no reason to be boxed, and a grid of them is the
banned SaaS pattern. Panels exist (the rail, a detail drawer) and are marked
by `bg-surface`, not by a border and a radius.

**Motion** is state, never decoration. Tokens already exist in
`frontend/lib/theme/tokens.dart`: 150ms `fast` for local state, 250ms
`standard` for most transitions, 350ms `emphasized` for large surfaces, with
M3 easing curves. Always resolve through `Motion.of(context)` so
`MediaQuery.disableAnimations` is honoured. Animate opacity and transform;
never animate layout properties, which force relayout on a 300-row list.
There is no page-load choreography: the app loads into a task.

## Components

**Review row** is the product. Proposal leads, evidence beneath:

```
  3 Aug   Rent income · 98A Sample Rd                    950.00
          SAMPLE ESTATES L 98A SAMPLE ROAD BGC
  ────────────────────────────────────────────────────────────
  3 Aug   Finance costs · 98A Sample Rd               −1,134.60
          TSB LOAN SERVICIN MORTGAGE STO
```

Line one is the agent's answer in `title` at `text-high`. Line two is the
raw bank narrative in `meta` at `text-muted`, always present, never
truncated to a tooltip. The amount is `numeric`, right-aligned, tabular.

Because the proposal leads, a **low-confidence row is the dangerous one**.
Low confidence sorts to the top of the queue and renders its proposal at
weight 400 with a `needs-you` marker, rather than the confident 600. It
looks less settled, which is exactly what it is.

**Status is a word plus a colour, never a colour.** `status-*` pills carry
the domain's own term: "proposed", "expiring", "categorisation failed".

**The category picker is a popover, not a modal.** Correcting a proposal
must not interrupt the queue: type to filter the fifteen HMRC categories,
Enter to apply, Escape to cancel, focus returns to the row.

**Keyboard is the primary input on the review screen**, not an enhancement.
`J`/`K` move, `Enter` accepts the proposal, `C` opens the category picker,
`X` excludes, `⇧`-click or `Space` selects for a batch confirm. Every focus
state is a visible 2px `accent` ring at 2px offset.

**Every interactive component ships all seven states**: default, hover,
focus, active, disabled, loading, error. Loading is a skeleton row matching
the review row's metrics, never a centred spinner.

**Empty states teach and orient.** Not "No results":

> **Nothing to review.**
> Your next quarterly update is due 7 November. Upload a statement when
> you're ready.

**Errors carry the backend's specificity through.** The parser names the
failing row, the export names the blocking transactions; the UI must not
flatten that:

> **Row 14 stopped this import.** Unparseable date `32/13/2026`. Nothing was
> saved, so fix that row and upload again.

> **3 lines still need a decision** before Q2 can be filed. [Review them]

**Moments** are earned, few, and quiet. The queue clearing (the list drains
to muted; one `display` line: *"That's the quarter reviewed. 47 lines, all
yours now."*). An export completing. Nothing lapsing in the next 60 days.
No confetti, ever: the subject is a tax return.

## Do's and Don'ts

**Do**

- Let settled work recede. The screen should be calmer at the end of a
  session than at the start.
- Pair every status colour with the domain's own word.
- Use tabular, slashed-zero figures for every number without exception.
- Keep the raw bank narrative visible on every review row. It is the
  evidence, and the user is checking a machine.
- Write errors that name the row, the field, and the next action.
- Right-align amounts; left-align everything else.

**Don't**

- Colour an amount by its sign. An expense is not an error.
- Colour confidence. Sort by it and weight it instead.
- Reach for a card. Hairline rules and spacing carry this layout; nested
  cards are always wrong.
- Open a modal to correct a category, or to do anything else the queue can
  absorb inline.
- Add a hero metric, a stat-card row, or a gradient. Named anti-reference.
- Use `#000`, `#fff`, gradient text, side-stripe borders, or glassmorphism.
- Animate a list into view on every navigation. Motion marks state changes,
  not arrivals.
- Fetch fonts at runtime on a page showing bank transactions.
