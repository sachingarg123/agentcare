/** Layered SVG waves that drift horizontally — used on auth hero. */
export function WaveField({ className }: { className?: string }) {
  return (
    <div className={`wave-field${className ? ` ${className}` : ""}`} aria-hidden="true">
      <svg className="wave-layer wave-a" viewBox="0 0 1440 120" preserveAspectRatio="none">
        <path
          fill="currentColor"
          d="M0,64 C240,110 480,10 720,50 C960,90 1200,20 1440,60 L1440,120 L0,120 Z"
        />
      </svg>
      <svg className="wave-layer wave-b" viewBox="0 0 1440 120" preserveAspectRatio="none">
        <path
          fill="currentColor"
          d="M0,70 C200,20 400,100 720,55 C1040,10 1240,90 1440,45 L1440,120 L0,120 Z"
        />
      </svg>
      <svg className="wave-layer wave-c" viewBox="0 0 1440 120" preserveAspectRatio="none">
        <path
          fill="currentColor"
          d="M0,50 C180,90 360,30 720,70 C1080,110 1260,40 1440,80 L1440,120 L0,120 Z"
        />
      </svg>
      <div className="pulse-trace">
        <svg viewBox="0 0 400 40" preserveAspectRatio="none">
          <path
            className="pulse-path"
            d="M0 20 H60 L70 20 L80 6 L95 34 L110 12 L125 20 H400"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>
    </div>
  );
}
