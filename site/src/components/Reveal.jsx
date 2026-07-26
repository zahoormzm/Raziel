import { useReveal } from "../hooks/useReveal";

export default function Reveal({
  as: Tag = "div",
  delay = 0,
  className = "",
  style,
  children,
  ...rest
}) {
  const [ref, visible] = useReveal();
  return (
    <Tag
      ref={ref}
      className={`reveal${visible ? " is-visible" : ""}${className ? ` ${className}` : ""}`}
      // `style` is pulled out of rest and merged. Spreading rest after a literal
      // style object let any caller passing its own style replace this one
      // wholesale, silently dropping --delay and un-delaying the reveal with no
      // error -- Sections.jsx already passes both.
      style={{ "--delay": `${delay}ms`, ...style }}
      {...rest}
    >
      {children}
    </Tag>
  );
}
