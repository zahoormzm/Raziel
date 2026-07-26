import { useEffect, useState } from "react";
import Reveal from "./Reveal";
import Scanner from "./Scanner";
import { useCountUp } from "../hooks/useReveal";
import {
  claims,
  evidenceStates,
  gates,
  metrics,
  refusals,
  rejectedLane,
  stages,
} from "../data";

/* ---------------------------------------------------------------- nav */

export function Nav() {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <nav className="nav" data-scrolled={scrolled}>
      <div className="shell nav-inner">
        <a className="wordmark" href="#top">
          RAZIEL <span>Temporal Evidence Intelligence</span>
        </a>
        <div className="nav-links">
          <a href="#states">Evidence</a>
          <a href="#pipeline">Pipeline</a>
          <a href="#results">Results</a>
          <a href="#contract">Contract</a>
        </div>
      </div>
    </nav>
  );
}

/* --------------------------------------------------------------- hero */

export function Hero() {
  return (
    <header className="shell hero" id="top">
      <div className="hero-grid">
        <div>
          <Reveal as="h1">
            Search recorded footage in plain language — and know <em>when it cannot see</em>.
          </Reveal>
          <Reveal className="hero-lede" as="p" delay={110}>
            RAZIEL searches a declared scope of surveillance video for zero, one, or many
            supported occurrences of what you describe. Its distinguishing property is not
            recall. It is that a result you cannot trust never looks like one you can.
          </Reveal>
          <Reveal className="hero-meta" delay={200}>
            <div>
              Runs on
              <b>One RTX 5070, locally</b>
            </div>
            <div>
              Indexes at
              <b>22.8× real time</b>
              <i>synthetic source</i>
            </div>
            <div>
              Parser correctness
              <b>96.8%, zero unsafe</b>
              <i>own 31-query set</i>
            </div>
          </Reveal>
        </div>
        <Reveal delay={150}>
          <Scanner />
        </Reveal>
      </div>
    </header>
  );
}

/* ------------------------------------------------------------- states */

export function States() {
  return (
    <section id="states">
      <div className="shell">
        <Reveal className="section-head">
          <p className="eyebrow">The difference</p>
          <h2>Four answers, not two</h2>
          <p>
            Most systems return a ranked list and let you infer the rest. Every required
            constraint here resolves independently to one of four states, and the last two are
            the ones that matter.
          </p>
        </Reveal>

        <div className="states">
          {evidenceStates.map((state, i) => (
            <Reveal className="state" key={state.id} delay={i * 90}>
              <div className="state-mark" style={{ color: `var(--${state.id})` }}>
                <i />
              </div>
              <h3 style={{ color: `var(--${state.id})` }}>{state.name}</h3>
              <p>{state.body}</p>
              <p className="state-note">{state.note}</p>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ----------------------------------------------------------- pipeline */

export function Pipeline() {
  const [active, setActive] = useState(0);
  const stage = stages[active];

  return (
    <section id="pipeline">
      <div className="shell">
        <Reveal className="section-head">
          <p className="eyebrow">How it works</p>
          <h2>Six stages, each with something it refuses to do</h2>
          <p>
            The constraints are the design. Every stage below is defined as much by what it
            declines to assert as by what it produces.
          </p>
        </Reveal>

        <div className="pipeline">
          <Reveal className="stage-list-wrap">
            <ul className="stage-list">
              {stages.map((item, i) => (
                <li className="stage" key={item.name} data-active={i === active}>
                  <button
                    onMouseEnter={() => setActive(i)}
                    onFocus={() => setActive(i)}
                    onClick={() => setActive(i)}
                    aria-current={i === active}
                  >
                    <span className="stage-index">{String(i + 1).padStart(2, "0")}</span>
                    <span className="stage-name">{item.name}</span>
                  </button>
                </li>
              ))}
            </ul>
          </Reveal>

          <Reveal className="stage-detail" delay={90}>
            <div key={active} className="fade-key">
              <h3>{stage.title}</h3>
              <p>{stage.body}</p>
              <div className="stage-guard">
                <b>{stage.guardLabel}</b>
                {stage.guard}
              </div>
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------ metrics */

function Metric({ item, delay }) {
  const [ref, value] = useCountUp(item.value, { decimals: item.decimals });
  return (
    <div className="metric" ref={ref} style={{ "--delay": `${delay}ms` }}>
      <div className="metric-value">
        {value}
        {item.unit && <span className="metric-unit">{item.unit}</span>}
      </div>
      <div className="metric-label">{item.label}</div>
      <div className="metric-note">{item.note}</div>
    </div>
  );
}

export function Results() {
  return (
    <section id="results">
      <div className="shell">
        <Reveal className="section-head">
          <p className="eyebrow">Measured</p>
          <h2>Numbers from this repository's own benchmark reports</h2>
          <p>
            Every figure below was produced by a run in this project and read back from its
            report file. Where something has not been measured, the configuration says
            <code style={{ fontFamily: "var(--mono)", fontSize: "0.85em" }}>
              {" "}
              not_yet_measured{" "}
            </code>
            rather than carrying a placeholder.
          </p>
        </Reveal>

        <div className="metrics">
          {metrics.map((item, i) => (
            <Metric item={item} key={item.label} delay={i * 70} />
          ))}
        </div>

        <Reveal className="refusals" style={{ marginTop: "clamp(3rem, 6vw, 4.5rem)" }}>
          <h3>{rejectedLane.name} — {rejectedLane.verdict}</h3>
          <p style={{ color: "var(--ink-soft)", maxWidth: "44rem", marginBottom: "1.75rem" }}>
            {rejectedLane.body}
          </p>
          <div className="metrics" data-cols="4">
            {rejectedLane.figures.map((f) => (
              <div className="metric" key={f.k}>
                <div className="metric-value" style={{ fontSize: "1.5rem" }}>
                  {f.v}
                </div>
                <div className="metric-note" style={{ marginTop: "0.6rem" }}>
                  {f.k}
                </div>
              </div>
            ))}
          </div>
        </Reveal>

        <Reveal>
          <table className="gates">
            <caption>Acceptance gates — current state, unrounded</caption>
            <thead>
              <tr>
                <th scope="col">Gate</th>
                <th scope="col">Status</th>
                <th scope="col">What remains</th>
              </tr>
            </thead>
            <tbody>
              {gates.map((gate) => (
                <tr key={gate.id}>
                  <td>
                    {gate.id} · {gate.name}
                  </td>
                  <td>
                    <span className="pill" data-tone={gate.tone}>
                      {gate.state}
                    </span>
                  </td>
                  <td>{gate.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Reveal>
      </div>
    </section>
  );
}

/* ----------------------------------------------------------- contract */

export function Contract() {
  return (
    <section id="contract">
      <div className="shell">
        <Reveal className="section-head">
          <p className="eyebrow">Truthfulness contract</p>
          <h2>What the system is allowed to claim</h2>
          <p>
            A guarantee nobody checks is a preference, so each of these is tied to
            something that runs: the schema validators in{" "}
            <code style={{ fontFamily: "var(--mono)", fontSize: "0.85em" }}>eval/schema.py</code>,
            the contract models, or a test in the suite. Where a claim rests on a gate that
            is still open, the table above says which one — it is the honest half of the
            same page.
          </p>
        </Reveal>

        <div className="contract-grid">
          {claims.map((claim, i) => (
            <Reveal className="claim" key={claim.title} delay={i * 80}>
              <h3>{claim.title}</h3>
              <p>{claim.body}</p>
            </Reveal>
          ))}
        </div>

        <Reveal className="refusals">
          <h3>And what it will not do</h3>
          <ul>
            {refusals.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </Reveal>
      </div>
    </section>
  );
}

/* -------------------------------------------------------------- quote */

export function Quote() {
  return (
    <section>
      <div className="shell">
        <Reveal className="quote" as="figure">
          <blockquote>
            “The camera could not see it” and “it did not happen” are different sentences.
            Most systems say the second when they mean the first.
          </blockquote>
          <figcaption>The premise of the project</figcaption>
        </Reveal>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------- footer */

export function Footer() {
  return (
    <footer>
      <div className="shell footer-inner">
        <div>
          <div className="footer-mark">RAZIEL</div>
          <p className="footer-note" style={{ marginTop: "0.75rem" }}>
            Local-first temporal evidence intelligence. Eyes of God names the recall-first
            retrieval subsystem only — never an omniscience claim.
          </p>
        </div>
        <p className="footer-note">
          Results are conditional on the declared scope and operating point. Export manifests
          are traceable extraction records, not claims of legal admissibility.
        </p>
      </div>
    </footer>
  );
}
