# ArthaSignal Design System

Status: IMPLEMENTED AND MAINTAINED
Scope: design tokens and access-control principles for the current Next.js frontend. The implementation lives primarily in `frontend/src/app/globals.css`, `frontend/src/app/layout.tsx`, and the page/component tree.

## Design Principle: Accent Coverage Constraint

The accent color (Deep Sage, `--color-accent-primary`) is a deliberate, narrow-use color, not a general-purpose brand color. It should cover roughly **10% of the UI surface** at most - CTAs/buttons, active nav states, the logo, and key highlight badges only. The remaining ~90% of any screen uses the neutral dark/light tokens below.

This is a hard constraint, not a suggestion. An interface where sage shows up on every card border, every heading, or every icon has failed this constraint - it produces an over-saturated look that undermines the accent's purpose of drawing the eye to the few things that matter (primary actions, current state, brand marks). When in doubt, default to a neutral token and reserve the accent for the single most important element on the screen.

## Access Control

Two tiers. Public market-data views support discoverability and broad access; user-specific state remains authenticated.

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
- Alerts when implemented
- User-specific assistant/bot integrations when implemented

Any new page must be explicitly classified into one of these two tiers before it ships. Market-data views default to public unless there is a concrete security, privacy, licensing, or product reason to gate them.

## Typography

| Use | Font |
|---|---|
| English, numbers, technical terms | IBM Plex Sans |
| Nepali text | Noto Sans Devanagari |

English is the default. Numeric/technical content (prices, tickers, indicator values, code) uses IBM Plex Sans for dense numeric/tabular display.

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
| `--color-warning` | `#F59E0B` | Warnings, caution states, unavailable/incomplete data |

Semantic colors carry consistent meaning across themes. Warning states are also used for reliability conditions such as incomplete portfolio valuation or temporarily unavailable market data.

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

The current frontend implements the token model in `frontend/src/app/globals.css`. The root declares theme-independent semantic colors; `[data-theme="dark"]` and `[data-theme="light"]` each declare their own neutral + accent set.

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

## Reliability UI rule

A missing upstream response must never be silently rendered as a valid zero/empty market state. Components should distinguish at least:

- successful data with values;
- successful data with a genuinely empty result;
- incomplete valuation / partially priced data; and
- unavailable upstream data.

This rule is especially important for signals, portfolio values, and any future alerting surface.
