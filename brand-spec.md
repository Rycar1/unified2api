# Unified console design

Reference sources: ARTEX `1791e5b61362f6d8aa0ea350aa1f23eda48a936e` and the live blog.rycarl.cn Fuwari theme, inspected on 2026-10-13.

- Purpose: a daily administration tool, legible on laptop, tablet and phone; quiet, precise and lightly translucent.
- Structure: ARTEX's stable navigation sidebar, restrained page headers and compact functional controls; no repeated add buttons or explanatory banners.
- Palette: neutral paper/slate surfaces, blue hue 250 from the blog's `--hue`; primary `oklch(0.7 0.14 250)`, deeper primary for text contrast. Dark mode uses slate backgrounds and translucent lighter surfaces.
- Typography: Geist-compatible Latin with Chinese system fallbacks; clear 14–16 px table text, 24–30 px page titles, tabular numeric counters.
- Spacing: 4 px grid; 12–16 px control gaps, 20–24 px panel padding.
- Radius: 10 px controls, 16 px panels from the blog's `--radius-large`, 20 px dialogs.
- Shadow: fine border plus low ambient shadow, stronger elevation only on menus and dialogs.
- Motion: interruptible 180–320 ms transitions, decelerating cubic curves, FLIP layout transitions for filtering and sorting, subtle opacity/position page changes; honor `prefers-reduced-motion`.
- Identity: reuse `images/unified2api-logo.png`; keep Unified's name and provider separation.
- States: loading skeletons, useful empty states, inline results/errors, visible keyboard focus, accessible dialogs and menus. Real counters only.
