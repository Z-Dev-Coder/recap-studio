# Recap Studio / Toolbox — Design Tokens

Design language specification: **Dark Mode Luxe + Bento Grid + Subtle Glassmorphism** (2025–2026 AI Creative Tools standard).

---

## 1. Core Principles

- **Calm & Clarity:** Generous whitespace, low visual noise, purposeful visual hierarchy.
- **Depth with Purpose:** Layered OKLCH darks, subtle box-shadows, and glassmorphism applied only where communicating elevation.
- **Premium Dark:** No harsh pure black (`#000`) or muddy warm tones. Crisp, cool-neutral layered darkness.
- **Content-First:** Asymmetric Bento Grid prioritizing primary workflows (`Recap Studio`).
- **Motion with Restraint:** Subtle micro-interactions (`120ms`–`200ms`), strictly obeying `prefers-reduced-motion`.

---

## 2. Color System (OKLCH Tokens)

### Base Surfaces (Layered Darkness)
| Token | OKLCH Value | Role / Usage |
|---|---|---|
| `--bg` | `oklch(0.145 0.005 270)` | Page base background |
| `--surface-1` | `oklch(0.18 0.006 270)` | Sidebar, module headers, nested panels |
| `--surface-2` | `oklch(0.22 0.007 270)` | Cards, Bento grid tiles, inputs |
| `--surface-3` | `oklch(0.26 0.008 270)` | Hover states, active pills, popovers |
| `--surface-4` | `oklch(0.31 0.009 270)` | Active selections, dialogs, modals |

### Borders
| Token | Value | Role |
|---|---|---|
| `--border` | `oklch(1 0 0 / 8%)` | Subtle boundary for cards and panels |
| `--border-strong` | `oklch(1 0 0 / 14%)` | Hover boundaries, active outlines |

### Typography & Text Contrast (WCAG AA Compliant)
| Token | OKLCH Value | Contrast vs `--bg` | Role |
|---|---|---|---|
| `--text-primary` | `oklch(0.97 0 0)` | ~14.2:1 | Headings, card titles, key values |
| `--text-secondary` | `oklch(0.72 0.01 270)` | ~6.5:1 | Body copy, descriptions, navigation labels |
| `--text-tertiary` | `oklch(0.55 0.01 270)` | ~4.6:1 | Metadata, timestamps, shortcuts, section labels |

### Accent Gradient (Cool Blue → Violet)
| Token | Hex / OKLCH | Role |
|---|---|---|
| `--accent-start` | `#6d8bff` | Gradient origin, primary focus ring |
| `--accent-mid` | `#8b7cff` | Gradient transition stop |
| `--accent-end` | `#a78bfa` | Gradient terminus, subtle highlights |
| `--accent` | `var(--accent-start)` | Base accent fallback |
| `--accent-soft` | `oklch(0.72 0.16 265 / 12%)` | Badge background, active tint |
| `--accent-glow` | `oklch(0.72 0.16 265 / 35%)` | Box-shadow aura, indicator glow |

### Semantic State Colors
| Token | Hex | Usage |
|---|---|---|
| `--success` | `#3ddc97` | Service ready, healthy pipeline |
| `--warning` | `#fbbf24` | Starting service, pending alert |
| `--danger` | `#f87171` | Service error, recording live indicator |
| `--info` | `#60a5fa` | Informational badges, audio layers |

### Glassmorphism
| Token | Value | Usage |
|---|---|---|
| `--glass-bg` | `rgba(255, 255, 255, 0.04)` | Subtle frosted glass overlay |
| `--glass-border` | `rgba(255, 255, 255, 0.08)` | Translucent edge reflection |
| `--glass-blur` | `20px` | Backdrop filter blur radius |

### Layered Shadows
| Token | CSS Definition | Usage |
|---|---|---|
| `--shadow-xs` | `0 1px 2px rgba(0, 0, 0, 0.2)` | Micro buttons, chips |
| `--shadow-sm` | `0 2px 8px rgba(0, 0, 0, 0.3)` | Bento cards resting state |
| `--shadow-md` | `0 8px 24px rgba(0, 0, 0, 0.4)` | Bento cards hover state |
| `--shadow-lg` | `0 16px 48px rgba(0, 0, 0, 0.5)` | Modals, floating sheets |
| `--shadow-glow`| `0 0 24px var(--accent-glow)` | Primary CTA, brand icon glow |

---

## 3. Typography Scale (Inter Font Family)

Font family: `"Inter", "Geist", system-ui, -apple-system, sans-serif`.

| Token | Size | Line-height | Weight | Letter-spacing | Usage |
|---|---|---|---|---|---|
| `--text-display` | `28px` | `1.2` | 700 | `-0.02em` | Page header ("Welcome back") |
| `--text-h1` | `20px` | `1.3` | 600 | `-0.01em` | Module titles, featured cards |
| `--text-h2` | `16px` | `1.4` | 600 | `-0.01em` | Standard card titles |
| `--text-body` | `14px` | `1.6` | 400 | `normal` | Body descriptions, form fields |
| `--text-caption` | `12px` | `1.4` | 500 | `normal` | Metadata, badge labels |
| `--text-micro` | `11px` | `1.4` | 600 | `0.08em` | Section headers (`uppercase`) |

---

## 4. Spacing Scale (4px Base Grid)

| Token | Pixels | Example Usage |
|---|---|---|
| `--space-1` | `4px` | Micro gaps, badge padding |
| `--space-2` | `8px` | Icon-to-text gap, chips |
| `--space-3` | `12px` | Card internal gap, small margins |
| `--space-4` | `16px` | Bento grid gap, card padding |
| `--space-5` | `20px` | Standard card padding |
| `--space-6` | `24px` | Featured card padding, section gap |
| `--space-8` | `32px` | Dashboard container padding |
| `--space-10` | `40px` | Large section spacing |
| `--space-12` | `48px` | Empty state padding |

---

## 5. Border Radius Scale

| Token | Value | Applied To |
|---|---|---|
| `--radius-xs` | `6px` | Shortcut keys (`b`), sub-buttons |
| `--radius-sm` | `8px` | Nav items, recent list rows |
| `--radius-md` | `14px` | Icon containers, inputs, mode cards |
| `--radius-lg` | `20px` | Bento cards, library media items |
| `--radius-xl` | `28px` | Module workspace frame |
| `--radius-full` | `9999px` | Buttons, badges, toggle tracks |

---

## 6. Motion & Animation Tokens

- **Timing Functions:**
  - `--ease-out`: `cubic-bezier(0.22, 1, 0.36, 1)` (Deceleration curve)
- **Durations:**
  - `--duration-fast`: `120ms` (Buttons, hover states)
  - `--duration-base`: `200ms` (Tab transitions, nav indicator scale)
  - `--duration-slow`: `320ms` (Modals, full view transitions)
- **Pulse Animation:**
  - `pulse-ring`: 2s infinite ease-out ring for active services (`oklch(0.8 0.15 155 / 40%)`)
- **Reduced Motion:**
  - When `prefers-reduced-motion: reduce` is enabled, all animations and transitions are clamped to `0.01ms`.

---

## 7. Responsive Bento Grid Layout

```css
/* >= 1200px: 4 columns */
.dashboard-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  grid-auto-rows: minmax(150px, auto);
  gap: var(--space-4);
}
.tool-card.featured { grid-column: span 2; grid-row: span 2; }
.tool-card.wide     { grid-column: span 2; }
.tool-card.tall     { grid-row: span 2; }

/* 800px - 1199px: 2 columns */
@media (max-width: 1199px) {
  .dashboard-grid { grid-template-columns: repeat(2, 1fr); }
  .tool-card.featured { grid-column: span 2; grid-row: span 2; }
  .tool-card.wide     { grid-column: span 2; }
}

/* < 800px: 1 column & collapsed sidebar */
@media (max-width: 799px) {
  .sidebar { width: 64px; }
  .dashboard-grid { grid-template-columns: 1fr; }
  .tool-card.featured, .tool-card.wide { grid-column: span 1; grid-row: auto; }
}
```
