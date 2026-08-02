# ArthaSignal Design System

Status: FINALIZED (supersedes any earlier draft)
Scope: documentation only - no frontend code exists yet. This defines the design tokens and access-control principles the future Next.js frontend will implement against.

## Design Principle: Accent Coverage Constraint

The accent color (Deep Sage, `--color-accent-primary`) is a deliberate, narrow-use color, not a general-purpose brand color. It should cover roughly **10% of the UI surface** at most - CTAs/buttons, active nav states, the logo, and key highlight badges only. The remaining ~90% of any screen uses the neutral dark/light tokens below.

This is a hard constraint, not a suggestion. An interface where sage shows up on every card border, every heading, or every icon has failed this constraint - it produces an over-saturated look that undermines the accent's purpose of drawing the eye to the few things that matter (primary actions, current state, brand marks). When in doubt, default to a neutral token and reserve the accent for the single most important element on the screen.

## Access Control

Two tiers. This is a real product constraint, not just a UX preference - the public tier exists both for SEO (indexable, crawlable pages) and because it satisfies TradingView's free Advanced Charts license terms, which require the charts to be publicly accessible without a login/paywall.

**Public (no login required):**
- Landing page
- Live charts
- Market pulse
- Stock technical pages
- Stock fundamental pages
- Sector performance pages

**Login required:**
- Portfolio
- Watchlist
- Alerts
- AI chatbot
- Bot linking

Any new page must be explicitly classified into one of these two tiers before it ships. Charts and market-data views default to public unless there's a specific reason to gate them - gating a chart page requires checking it doesn't violate the TradingView license terms above.

## Typography

| Use | Font |
|---|---|
| English, numbers, technical terms | IBM Plex Sans |
| Nepali text | Noto Sans Devanagari |

English is the default; Nepali is a user toggle. Numeric/technical content (prices, tickers, indicator values, code) always renders in IBM Plex Sans regardless of the active language toggle, since Noto Sans Devanagari is not designed for dense numeric/tabular display.

```css
:root {
  --font-latin: "IBM Plex Sans", sans-serif;
  --font-devanagari: "Noto Sans Devanagari", sans-serif;
}
```

## Color Tokens

Dark is the default theme. Light is a toggle. Both are defined as complete token sets - no token should be looked up in one theme and fall back to the other.

### Accent (use sparingly - see Design Principle above)

| Token | Hex | Use |
|---|---|---|
| `--color-accent-primary` | `#4F5F45` | Buttons, active states, logo, key highlights |
| `--color-accent-primary-light` | `#A9C29A` | Hover/lighter variant of the accent |

### Semantic (trading-specific)

| Token | Hex | Use |
|---|---|---|
| `--color-success` | `#7DD3A8` | Price up / gains |
| `--color-danger` | `#EF4444` | Price down / losses |
| `--color-warning` | `#F59E0B` | Warnings, caution states |

Semantic colors are theme-independent - they carry the same meaning and roughly the same hex value in both dark and light mode, since "green means up, red means down" must stay consistent regardless of theme.

### Neutral - Dark Theme (default)

| Token | Hex |
|---|---|
| `--color-background` | `#121316` |
| `--color-card` | `#1B1D21` |
| `--color-border` | `#2A2D33` |
| `--color-text-primary` | `#EDEEF0` |
| `--color-text-secondary` | `#9A9CA3` |

### Neutral - Light Theme (toggle)

| Token | Hex |
|---|---|
| `--color-background` | `#FAF7F1` |
| `--color-card` | `#F5F1E8` |
| `--color-border` | `#E5DFD1` |
| `--color-text-primary` | `#211F1B` |
| `--color-text-secondary` | `#6B6659` |

## CSS Custom Properties

Ready for the future Next.js frontend. The root declares font tokens and theme-independent semantic colors; `[data-theme="dark"]` and `[data-theme="light"]` each declare their own complete neutral + accent set.

```css
:root {
  --font-latin: "IBM Plex Sans", sans-serif;
  --font-devanagari: "Noto Sans Devanagari", sans-serif;

  --color-success: #7DD3A8;
  --color-danger: #EF4444;
  --color-warning: #F59E0B;
}

[data-theme="dark"] {
  --color-accent-primary: #4F5F45;
  --color-accent-primary-light: #A9C29A;

  --color-background: #121316;
  --color-card: #1B1D21;
  --color-border: #2A2D33;
  --color-text-primary: #EDEEF0;
  --color-text-secondary: #9A9CA3;
}

[data-theme="light"] {
  --color-accent-primary: #4F5F45;
  --color-accent-primary-light: #A9C29A;

  --color-background: #FAF7F1;
  --color-card: #F5F1E8;
  --color-border: #E5DFD1;
  --color-text-primary: #211F1B;
  --color-text-secondary: #6B6659;
}
```
