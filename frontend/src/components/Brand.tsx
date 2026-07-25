import { APP_NAME } from "../brand";

type Props = {
  size?: number;
  className?: string;
  title?: string;
};

/** Pulse line mark — ECG-style stroke for PulseDesk. */
export function BrandMark({ size = 36, className, title = APP_NAME }: Props) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label={title}
    >
      <rect width="48" height="48" rx="12" fill="var(--accent)" />
      <path
        d="M8 26h7l3-8 5 16 4-12 3 6h10"
        stroke="#fff"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="38" cy="26" r="2.2" fill="#fff" />
    </svg>
  );
}

type WordmarkProps = {
  size?: "sm" | "md" | "lg";
  className?: string;
};

export function BrandWordmark({ size = "md", className }: WordmarkProps) {
  const mark = size === "lg" ? 48 : size === "sm" ? 28 : 34;
  return (
    <span className={`brand-wordmark brand-wordmark-${size}${className ? ` ${className}` : ""}`}>
      <BrandMark size={mark} />
      <span className="brand-wordmark-text">{APP_NAME}</span>
    </span>
  );
}
