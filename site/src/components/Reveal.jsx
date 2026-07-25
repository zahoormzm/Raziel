import { useReveal } from "../hooks/useReveal";

export default function Reveal({ as: Tag = "div", delay = 0, className = "", children, ...rest }) {
  const [ref, visible] = useReveal();
  return (
    <Tag
      ref={ref}
      className={`reveal${visible ? " is-visible" : ""}${className ? ` ${className}` : ""}`}
      style={{ "--delay": `${delay}ms` }}
      {...rest}
    >
      {children}
    </Tag>
  );
}
