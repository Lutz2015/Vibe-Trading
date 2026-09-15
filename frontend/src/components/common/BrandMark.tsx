export function BrandMark({
  className = "h-6 w-6",
}: {
  className?: string;
}) {
  // Shared tech mark with desktop icon: deep slate tile, hex frame,
  // cyan→emerald data trajectory with four nodes.
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 64 64"
      fill="none"
      className={className}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id="pt-bg" x1="8" y1="8" x2="56" y2="56" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#0A101C" />
          <stop offset="100%" stopColor="#0F1C2E" />
        </linearGradient>
        <linearGradient id="pt-line" x1="16" y1="48" x2="48" y2="18" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#22D3EE" />
          <stop offset="100%" stopColor="#34D399" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="14" fill="url(#pt-bg)" />
      <rect x="1" y="1" width="62" height="62" rx="13" stroke="#22D3EE" strokeOpacity="0.28" strokeWidth="2" />
      <path
        d="M32 11 50.5 21.7v21.6L32 54 13.5 43.3V21.7L32 11Z"
        stroke="#94A3B8"
        strokeOpacity="0.55"
        strokeWidth="1.6"
        fill="#0F172A"
        fillOpacity="0.45"
      />
      <path
        d="M18 43.5 27 33.5 35 37.5 46 22.5"
        stroke="url(#pt-line)"
        strokeWidth="3.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="18" cy="43.5" r="3" fill="#22D3EE" />
      <circle cx="27" cy="33.5" r="3" fill="#67E8F9" />
      <circle cx="35" cy="37.5" r="3" fill="#6EE7B7" />
      <circle cx="46" cy="22.5" r="3.4" fill="#34D399" />
      <circle cx="46" cy="22.5" r="6.2" stroke="#34D399" strokeOpacity="0.4" strokeWidth="1.4" />
    </svg>
  );
}
