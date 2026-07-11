# iPad layout testing

Automated Playwright coverage lives in `frontend/tests/e2e/tablet-layout.spec.ts` and runs in CI via `npm run test:e2e`.

CI uses Chromium desktop emulation at tablet viewports. It does **not** replace verification on real iPad Safari and iPad Chrome.

## Automated viewport smoke tests

| Viewport | Purpose |
| --- | --- |
| 1024 × 768 | iPad 10.2 landscape |
| 1180 × 820 | iPad Air landscape |
| 1194 × 834 | iPad Pro 11 landscape |
| 1366 × 1024 | iPad Pro 12.9 landscape |
| 768 × 1024 | iPad portrait (stacked shell) |

Assertions include:

- Two-column sidebar shell at landscape widths ≥ 1024px
- Single-column shell at portrait widths ≤ 959px
- Manual entry primary form grid stays two columns at 1024px
- Dashboard metric cards use a multi-column grid at 1024px

## Manual checklist (real devices)

Run on **iPad Safari** and **iPad Chrome** in both orientations.

### Global

- [ ] No pinch zoom needed on dashboard, transactions, data entry, or batch review
- [ ] Bottom content is not clipped when the browser chrome expands/collapses
- [ ] Safe-area padding looks correct in landscape with home indicator
- [ ] Orientation change does not break layout or trap scroll

### Dashboard

- [ ] Landscape: sidebar visible beside content; summary cards and charts readable
- [ ] Portrait: cards stack comfortably; charts remain legible

### Transactions

- [ ] Landscape: table readable; horizontal scroll works if needed
- [ ] Portrait: important columns visible without zoom

### Data entry

- [ ] Landscape: manual form uses two columns for primary fields
- [ ] File upload button is easy to tap; drop zone fits width
- [ ] Date and select controls open without layout jump

### Batch review

- [ ] Review table scrolls horizontally on narrow widths
- [ ] Row controls and selects are at least ~44px tall

### Settings / AI insights

- [ ] Sidebar shell matches other pages at landscape width
- [ ] Form fields and buttons are touch-friendly

### Keyboard / trackpad (optional)

- [ ] Magic Keyboard focus rings visible on links and buttons
- [ ] Form fields do not trigger unwanted page zoom (font size ≥ 16px on inputs)
