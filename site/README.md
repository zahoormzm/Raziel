# RAZIEL — project site

A static React site describing the system. Built with Vite.

```bash
npm install
npm run dev      # http://localhost:5183
npm run build    # -> site/dist
```

## Content policy

Every number on this site is a measured result, collected in
[`src/data.js`](src/data.js) with its source named there.

**Where to check them.** The originating benchmark reports live under
`artifacts/`, which is gitignored — recorded footage and model outputs do not
belong in git history. So the citable source for anyone who clones this
repository is [`../RELEASE_STATUS.md`](../RELEASE_STATUS.md), which is tracked
and carries every figure in its "Measured hardware results" and "Gate status"
tables. `eval/reports/g3_audit.json` is also tracked and is the machine-readable
source of the 96.8% parser figure.

Nothing is estimated, rounded up, or invented. Where a thing has not been
measured, the site says so rather than showing a placeholder — including the
Qwen3-VL 8B lane, which is reported as measured and **not** shipped because it
failed its VRAM headroom gate.

If you change a benchmark, update `RELEASE_STATUS.md` and `src/data.js`
together. Do not hand-write a figure into either.

## Design notes

- One accent colour, warm neutrals, generous whitespace. No gradients, no glow.
- Serif display face for headings, system sans for body, mono for data.
- Motion is used to direct attention: scroll reveals fire once and never repeat,
  and everything is disabled under `prefers-reduced-motion` — subscribed to live,
  so toggling the OS setting takes effect without a reload.
- Light and dark are both first-class, driven by `prefers-color-scheme`.
