# RAZIEL — project site

A static React site describing the system. Built with Vite.

```bash
npm install
npm run dev      # http://localhost:5183
npm run build    # -> site/dist
```

## Content policy

Every number on this site is read from a benchmark report in this repository —
`artifacts/b1/report.json`, `artifacts/b2/report.json`, `artifacts/b3/report.json`,
`artifacts/b5/report.json`, and `eval/reports/g3_audit.json`. They live in
[`src/data.js`](src/data.js).

Nothing is estimated, rounded up, or invented. Where a thing has not been
measured, the site says so rather than showing a placeholder — including the
Qwen3-VL 8B lane, which is reported as measured and **not** shipped because it
failed its VRAM headroom gate.

If you change a benchmark, update `src/data.js` from the report file. Do not
hand-write a figure.

## Design notes

- One accent colour, warm neutrals, generous whitespace. No gradients, no glow.
- Serif display face for headings, system sans for body, mono for data.
- Motion is used to direct attention: scroll reveals fire once and never repeat,
  and everything is disabled under `prefers-reduced-motion`.
- Light and dark are both first-class, driven by `prefers-color-scheme`.
