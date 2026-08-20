# FootNotes Design System

**Status:** Foundation specification  
**Scope:** FootNotes extension popup, onboarding/status UI, Recall, and supporting product surfaces  
**Principle:** Warm editorial clarity for human memory, precise machine language for local system state.

---

## 1. Brand registers

FootNotes uses three distinct typographic registers. Keep their roles separate; the contrast between them is part of the identity.

1. **Interface — Inter Tight**  
   The default face for UI, headings, controls, labels, and factual values. Direct, compact, and quiet.
2. **Voice — Newsreader**  
   Reserved for the product's human voice: the tagline, the running status word, and lead paragraphs. Do not use it for ordinary UI labels, card titles, or machine state. Tagline and status are italic; lead paragraphs are roman.
3. **Machine — IBM Plex Mono**  
   Used for system metadata and compact technical language: eyebrows, fact labels, file paths, versions, keycaps, logs, and similar machine-readable values.

All three families are Google Fonts. Ship or self-host the exact weights used by the product so the extension and local surfaces do not depend on a live font request.

## 2. Design tokens

### 2.1 Typography

| Token | Family | Weight/style | Size | Tracking | Use |
|---|---|---:|---:|---:|---|
| `type.headline` | Inter Tight | 700 | 40px | -0.035em | Page and card headline |
| `type.section` | Inter Tight | 600 | 19px | -0.02em | Section heading |
| `type.body` | Inter Tight | 400 | 13.5px | normal | Default UI copy |
| `type.fact-value` | Inter Tight | 500 | 13.5px | normal | Fact-row values |
| `type.lead` | Newsreader | 400 roman | 16.5px | normal | Lead paragraph only |
| `type.voice` | Newsreader | 400 italic | context-specific | normal | Tagline and running status word only |
| `type.eyebrow` | IBM Plex Mono | 500 | 10px | 0.14em | Uppercase eyebrow |
| `type.fact-label` | IBM Plex Mono | 400 | 10.5px | 0.06em | Fact labels |
| `type.machine` | IBM Plex Mono | 400 | 11–12px | normal | Paths, version, log-like data |
| `type.keycap` | IBM Plex Mono | 500 | 11px | normal | Keyboard shortcut chip |

Recommended line heights:

- Headline: `0.98–1.04`, depending on wrap.
- Section head: `1.2`.
- Body and facts: `1.45`.
- Lead: `1.5`.
- Eyebrows and compact machine data: `1.2`.

Use tabular numerals for counts, durations, versions, and aligned numeric facts.

### 2.2 Color

No colors outside this palette should be introduced without extending this specification.

| Token | Value | Role |
|---|---:|---|
| `color.page` | `#FAF6EF` | Page background |
| `color.surface` | `#FFFDF9` | Raised cards and inner surfaces |
| `color.recessed` | `#F2EADD` | Recessed panels and grouped controls |
| `color.line` | `#ECE4D8` | Default hairline |
| `color.line-soft` | `#F0E9DE` | Subtle hairline / low-emphasis division |
| `color.line-dashed` | `#DED4C4` | Dashed ambient-note border |
| `color.ink` | `#17150F` | Primary text and dark geometry |
| `color.secondary` | `#4A4438` | Secondary headings and strong supporting copy |
| `color.body` | `#6B6357` | Body copy |
| `color.muted` | `#8A8073` | Metadata and quiet labels |
| `color.quiet` | `#A09686` | Disabled or lowest-emphasis text |
| `color.accent` | `#E2542A` | Eyebrow, asterisk, enrichment dot, keycap base edge |
| `color.plum` | `#3D1B24` | Primary action and destructive action |
| `color.success` | `#4F9D6E` | Healthy/online state and “Saved ✓” |
| `color.success-strong` | `#3F7D59` | High-contrast green text or active health state |

Usage constraints:

- Accent orange calls attention; it is not the default button fill.
- Plum is the solid action color. If primary and destructive actions appear together, they must be distinguished by label, placement, and confirmation—not by inventing a new color.
- Green is reserved for confirmed success or healthy connection state.
- Quiet text is supporting information only; never use it for essential instructions.
- Borders are always 1px.

### 2.3 Geometry and elevation

| Token | Value | Use |
|---|---:|---|
| `radius.card` | 20px | Main cards |
| `radius.dashed` | 16px | Dashed ambient-note tile |
| `radius.tile` | 12px | Inner tiles and callouts |
| `radius.button` | 10px | Buttons and inputs |
| `radius.keycap` | 6px | Keycap chips |
| `border.default` | 1px | All borders and dividers |
| `layout.gap` | 20px | Main column gap |
| `layout.max` | 1000px | Content maximum width |
| `layout.columns` | 1.15fr 1fr | Desktop two-column grid |

Do not use drop shadows. The only elevation cue is the keycap's 1px orange base edge. Surfaces are separated by background color, border, spacing, and containment.

## 3. Layout

The primary desktop canvas is centered, no wider than `1000px`, with a `1.15fr 1fr` grid and `20px` column gap. Use the wider column for narrative or primary state and the narrower column for facts, steps, or supporting actions.

Recommended responsive behavior:

- At or below `760px`, collapse to one column.
- Preserve document order: headline/lead, primary state, facts, next steps, ambient notes.
- Use 20px outer page padding on compact layouts and at least 32px on desktop.
- Fact rows retain the 150px label column when space allows; below approximately `520px`, stack label above value.
- Controls may wrap, but the primary action should remain first in reading order.

## 4. Components

### 4.1 Eyebrow + headline block

Structure:

1. Optional IBM Plex Mono uppercase eyebrow in orange.
2. Inter Tight headline at 40px/700.
3. Optional Newsreader lead at 16.5px roman.

Use one block per major page or card. Keep the eyebrow short and factual. Keep the lead to one or two sentences.

### 4.2 Fact rows

Fact lists use a two-column grid: `150px minmax(0, 1fr)`. Labels use `type.fact-label`; values use `type.fact-value`. Every row after the first is separated by a 1px hairline. Long paths and machine values may wrap anywhere, but human-readable values should wrap by word.

Use semantic `dl`, `dt`, and `dd` markup when implemented on the web.

### 4.3 Actions

Three action tiers are available:

- **Solid:** Plum background, light text. Use for the single primary action or a clearly destructive action.
- **Ghost:** Transparent or surface background with a 1px hairline. Use for secondary actions.
- **Text link:** IBM Plex Mono, no container. Use for logs, reveal/open actions, or low-emphasis navigation.

Buttons use a 10px radius and a minimum 40px target height. Use explicit verb-first labels. Do not place two solid buttons in one action group.

### 4.4 Numbered-step list

Each step begins with a 20px circular ink marker containing a centered light numeral. Step copy uses body text; optional supporting machine data uses IBM Plex Mono. Maintain a clear vertical rhythm and do not connect circles with decorative lines.

### 4.5 Keycap chip

Use IBM Plex Mono at 11px/500 inside a 6px-radius chip. The chip has an ink fill or surface fill according to context, a 1px border, and a single 1px orange base edge. It is a hint, not an interactive button.

### 4.6 Callout tile

Use a surface tile with a 12px radius inside a recessed panel. A success callout may use `color.success-strong` for the confirmation label (“Saved ✓”), while its explanatory text remains body-colored. Callouts should confirm an outcome or explain the next state, not repeat nearby copy.

### 4.7 Ambient note

Ambient notes use a transparent/page background, 1px dashed `color.line-dashed` border, and 16px radius. Pair a 7px orange enrichment dot with low-emphasis body copy. These notes explain background activity and should never contain the primary action.

### 4.8 Connection signal

The shared connection signal consists of three 2px-wide rounded bars with increasing heights. It sits beside the Newsreader italic running status word.

States:

- **Connecting:** Bars animate in sequence using ink or muted color.
- **Healthy:** Bars are steady green.
- **Offline/error:** Bars are steady muted; the adjacent status text carries the explicit state. Do not rely on color alone.

Use a subtle height/opacity pulse, approximately `900ms–1200ms`, staggered across the three bars. Under `prefers-reduced-motion: reduce`, show the final static arrangement with no animation.

## 5. Interaction states

Every interactive component must define:

- Rest, hover, focus-visible, active, disabled, loading, success, and error where applicable.
- A 2px focus-visible outline with sufficient offset. Reuse ink, plum, or orange from the palette; do not remove focus indication.
- Disabled controls that remain legible and cannot be confused with loading.
- Progress language that describes the action (“Saving…”, “Connecting…”) and a persistent outcome where needed (“Saved ✓”).

Keep motion between `120ms` and `180ms` for hover/press transitions. Prefer color and 1px translation; avoid scaling, spring motion, and decorative looping animation. The connection signal is the intentional exception.

## 6. Content and voice

- Use sentence case for headings, labels, and buttons. Eyebrows are uppercase by styling, not hard-coded copy.
- Prefer direct, calm verbs: Save, Open folder, View log, Try again.
- Treat local ownership as a product promise: explain where data lives and what background work is happening.
- Avoid anthropomorphizing enrichment or implying certainty the system does not have.
- Paths, versions, shortcuts, durations, counts, and provider names use the machine register.
- The Newsreader voice register should feel human and reflective, never ornamental.

## 7. Accessibility requirements

- Meet WCAG 2.2 AA contrast for all text and interactive states. Verify small muted and quiet text against each background before release.
- Minimum interactive target: 40×40px; prefer 44×44px on touch-first surfaces.
- Preserve keyboard access and logical focus order.
- Never communicate healthy, destructive, selected, or error state by color alone.
- Support 200% text zoom without clipping, overlap, or lost actions.
- Respect `prefers-reduced-motion`.
- Provide accessible names for icon-only controls and announce asynchronous status changes with an appropriate live region.

## 8. Implementation starter tokens

```css
:root {
  --font-ui: "Inter Tight", sans-serif;
  --font-voice: "Newsreader", serif;
  --font-mono: "IBM Plex Mono", monospace;

  --page: #faf6ef;
  --surface: #fffdf9;
  --recessed: #f2eadd;
  --line: #ece4d8;
  --line-soft: #f0e9de;
  --line-dashed: #ded4c4;
  --ink: #17150f;
  --secondary: #4a4438;
  --body: #6b6357;
  --muted: #8a8073;
  --quiet: #a09686;
  --accent: #e2542a;
  --plum: #3d1b24;
  --success: #4f9d6e;
  --success-strong: #3f7d59;

  --radius-card: 20px;
  --radius-dashed: 16px;
  --radius-tile: 12px;
  --radius-button: 10px;
  --radius-keycap: 6px;
  --layout-gap: 20px;
  --layout-max: 1000px;
}
```

## 9. Definition of done

A FootNotes surface is ready when:

- It uses the three type registers according to role.
- It uses only the approved palette and 1px borders.
- It introduces no general drop shadow.
- Components match the radius hierarchy.
- All interactive and async states are specified and testable.
- The two-column layout collapses cleanly and facts remain readable on compact widths.
- Contrast, keyboard flow, zoom, and reduced motion have been verified.
- Connection status is understandable without animation or color.

## 10. Open decisions

The foundation is coherent. The following details still need an explicit product decision before this can be treated as a complete production system:

1. **Font delivery:** exact self-hosted Google Fonts files and licensing/OFL notices for extension and local-app packaging.
2. **Spacing scale:** a small canonical scale (for example 4, 8, 12, 16, 20, 24, 32, 40) so padding and vertical rhythm do not drift.
3. **Responsive breakpoint:** confirm the one-column breakpoint after testing actual content; `760px` is the recommended starting point.
4. **Focus treatment:** select the exact palette token and offset used globally, then test contrast on all three backgrounds.
5. **State copy:** canonical labels for connecting, offline, retrying, saved, queued, and enrichment-disabled states.
6. **Destructive-action pattern:** confirmation threshold, undo behavior, and whether destructive actions share plum with the primary action in the same view.
7. **Icon system:** source, stroke weight, default sizes, and when text labels are mandatory.
8. **Density and overflow:** truncation/wrapping rules for long titles, paths, providers, and localized text.
9. **Dark browser surround:** whether the extension popup keeps its existing dark outer frame; it is not part of the supplied palette and should be treated as host chrome, not a design token.
10. **Migration scope:** the current product still uses Instrument Sans/Serif and nearby—but not identical—color values. Confirm whether this document is the target for a staged migration across popup, onboarding, and Recall.

