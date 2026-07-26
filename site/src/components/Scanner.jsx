import { useEffect, useRef, useState } from "react";

/** An ILLUSTRATION of a sweep across a frame grid, revealing evidence regions as
 *  it passes. The regions and the readout below are hardcoded, not a real scan —
 *  the caption says so, because this sits in a project whose discipline is
 *  `not_yet_measured` over placeholder values, and a screenshot of a fabricated
 *  readout in the product's own output format would be indistinguishable from a
 *  real one.
 *
 *  The sweep is driven by a CSS custom property written straight to the DOM
 *  rather than React state: committing state every frame re-rendered this
 *  component and its children ~60 times a second for the life of the page, for a
 *  decorative element. It also pauses off screen and follows
 *  prefers-reduced-motion changes live. */

const REGIONS = [
  { x: 14, y: 22, w: 17, h: 30, label: "person" },
  { x: 38, y: 46, w: 12, h: 20, label: "bag · black" },
  { x: 58, y: 16, w: 20, h: 34, label: "person" },
  { x: 72, y: 58, w: 15, h: 24, label: "gate" },
];

const CYCLE_MS = 9000;

export default function Scanner() {
  const rootRef = useRef(null);
  const sweepRef = useRef(null);
  const readoutRef = useRef(null);
  const frameRef = useRef(0);
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => setReduced(query.matches);
    onChange();
    // Subscribed, not sampled once at mount. Reduced motion is the setting most
    // likely to be toggled while a page is open, precisely because the motion is
    // what prompts it.
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;

    const paint = (progress) => {
      if (sweepRef.current) sweepRef.current.style.left = `${progress}%`;
      const seen = REGIONS.filter((r) => progress >= r.x).length;
      for (const node of root.querySelectorAll("[data-region-x]")) {
        const x = Number(node.dataset.regionX);
        const w = Number(node.dataset.regionW);
        node.dataset.state =
          progress < x ? "unseen" : progress < x + w + 22 ? "seen" : "past";
      }
      if (readoutRef.current) {
        readoutRef.current.textContent =
          `cam_01 · ${((progress / 100) * 60).toFixed(1)}s / 60.0s · ` +
          `${seen} region${seen === 1 ? "" : "s"} · illustration`;
      }
    };

    if (reduced) {
      paint(100);
      return;
    }

    let start;
    let running = true;
    const tick = (now) => {
      if (!running) return;
      if (start === undefined) start = now;
      paint((((now - start) % CYCLE_MS) / CYCLE_MS) * 100);
      frameRef.current = requestAnimationFrame(tick);
    };

    // Only animate while on screen; a hero animation has no business burning
    // frames once the reader has scrolled past it.
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !running) {
          running = true;
          start = undefined;
          frameRef.current = requestAnimationFrame(tick);
        } else if (!entry.isIntersecting && running) {
          running = false;
          cancelAnimationFrame(frameRef.current);
        }
      },
      { threshold: 0 },
    );
    observer.observe(root);
    frameRef.current = requestAnimationFrame(tick);

    return () => {
      running = false;
      cancelAnimationFrame(frameRef.current);
      observer.disconnect();
    };
  }, [reduced]);

  return (
    <figure
      className="scanner"
      ref={rootRef}
      aria-label="Illustration of a search sweeping a declared camera scope. Not a real scan result."
    >
      <div className="scanner-grid" aria-hidden="true" />

      {REGIONS.map((region) => (
        <div
          key={region.label + region.x}
          className="scanner-track"
          data-state="unseen"
          data-region-x={region.x}
          data-region-w={region.w}
          style={{
            left: `${region.x}%`,
            top: `${region.y}%`,
            width: `${region.w}%`,
            height: `${region.h}%`,
          }}
        >
          <b>{region.label}</b>
        </div>
      ))}

      {!reduced && <div className="scanner-sweep" ref={sweepRef} aria-hidden="true" />}

      <figcaption className="scanner-readout">
        <span ref={readoutRef}>cam_01 · 0.0s / 60.0s · 0 regions · illustration</span>
      </figcaption>
    </figure>
  );
}
