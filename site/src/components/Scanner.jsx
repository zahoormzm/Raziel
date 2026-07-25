import { useEffect, useRef, useState } from "react";

/** A sweep across a frame grid that reveals evidence regions as it passes.
 *  The motion is the product's actual behaviour — scanning a declared scope —
 *  not decoration. It runs once per cycle, slowly, and stops entirely under
 *  prefers-reduced-motion. */

const REGIONS = [
  { x: 14, y: 22, w: 17, h: 30, label: "person" },
  { x: 38, y: 46, w: 12, h: 20, label: "bag · black" },
  { x: 58, y: 16, w: 20, h: 34, label: "person" },
  { x: 72, y: 58, w: 15, h: 24, label: "gate" },
];

const CYCLE_MS = 9000;

export default function Scanner() {
  const [progress, setProgress] = useState(0);
  const [reduced, setReduced] = useState(false);
  const frameRef = useRef();

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (query.matches) {
      setReduced(true);
      setProgress(100);
      return;
    }

    let start;
    const tick = (now) => {
      if (start === undefined) start = now;
      const elapsed = (now - start) % CYCLE_MS;
      setProgress((elapsed / CYCLE_MS) * 100);
      frameRef.current = requestAnimationFrame(tick);
    };
    frameRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frameRef.current);
  }, []);

  const seenCount = REGIONS.filter((r) => progress >= r.x).length;
  const elapsedSeconds = ((progress / 100) * 60).toFixed(1);

  return (
    <figure className="scanner" aria-label="A search sweeping a declared camera scope">
      <div className="scanner-grid" aria-hidden="true" />

      {REGIONS.map((region) => {
        const passed = progress >= region.x;
        const recent = passed && progress < region.x + region.w + 22;
        return (
          <div
            key={region.label + region.x}
            className="scanner-track"
            data-state={!passed ? "unseen" : recent ? "seen" : "past"}
            style={{
              left: `${region.x}%`,
              top: `${region.y}%`,
              width: `${region.w}%`,
              height: `${region.h}%`,
            }}
          >
            <b>{region.label}</b>
          </div>
        );
      })}

      {!reduced && (
        <div className="scanner-sweep" style={{ left: `${progress}%` }} aria-hidden="true" />
      )}

      <figcaption className="scanner-readout">
        <span>cam_01 · {elapsedSeconds}s / 60.0s</span>
        <span>
          {seenCount} region{seenCount === 1 ? "" : "s"} observed
        </span>
      </figcaption>
    </figure>
  );
}
