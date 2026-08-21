/**
 * Decorative navy backdrop: realistic faint warship / aircraft / missile silhouettes.
 * Full shapes stay inside the viewBox. pointer-events: none.
 */
export default function TacticalBackdrop() {
  // Soft behind UI but shapes stay recognizable.
  const ink = "rgba(126,182,255,0.1)";
  const inkDim = "rgba(76,154,255,0.085)";
  const inkSoft = "rgba(103,232,249,0.075)";

  return (
    <div
      aria-hidden
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 0,
        pointerEvents: "none",
        overflow: "hidden",
      }}
    >
      <svg
        width="100%"
        height="100%"
        viewBox="0 0 1440 900"
        preserveAspectRatio="xMidYMid meet"
        xmlns="http://www.w3.org/2000/svg"
        style={{ display: "block", width: "100%", height: "100%" }}
      >
        {/* Guided missile destroyer — side profile, bottom-left */}
        <g transform="translate(70 655)" fill={ink}>
          <path d="M12 58 L48 58 L56 48 L78 48 L86 40 L112 40 L118 34 L168 34 L176 40 L248 40 L258 48 L292 48 L300 54 L300 62 L288 66 L52 66 L44 62 L12 62 Z" />
          <path d="M120 34 L128 18 L142 18 L148 28 L162 28 L168 34 Z" />
          <path d="M172 34 L178 22 L192 22 L198 28 L210 28 L216 34 Z" />
          <rect x="134" y="6" width="3.5" height="14" rx="0.6" />
          <rect x="184" y="8" width="3" height="14" rx="0.6" />
          <path d="M230 40 L242 28 L256 28 L266 40 Z" />
          <circle cx="236" cy="34" r="3.2" />
          <path d="M64 48 L72 42 L80 42 L86 48 Z" />
        </g>

        {/* Aircraft carrier — angled deck silhouette, lower-right */}
        <g transform="translate(760 575)" fill={inkDim}>
          <path d="M8 52 L36 36 L92 28 L280 28 L320 40 L328 52 L328 64 L300 72 L48 72 L12 64 Z" />
          <path d="M100 28 L108 14 L148 14 L156 28 Z" />
          <rect x="168" y="18" width="70" height="8" rx="1.2" />
          <path d="M250 28 L262 20 L278 20 L286 28 Z" />
          <rect x="70" y="42" width="8" height="4" rx="0.5" opacity="0.7" />
          <rect x="190" y="42" width="10" height="4" rx="0.5" opacity="0.7" />
        </g>

        {/* Multirole fighter (F-16 style) — upper-right, full planform */}
        <g transform="translate(1080 155) rotate(-6)" fill={ink}>
          <path d="M78 40 L18 46 L0 48 L18 50 L78 56 L68 48 Z" />
          <path d="M40 40 L22 12 L34 14 L54 40 Z" />
          <path d="M40 56 L22 84 L34 82 L54 56 Z" />
          <path d="M72 44 L98 40 L104 42 L104 54 L98 56 L72 52 Z" />
          <path d="M88 42 L96 28 L102 30 L96 44 Z" />
          <path d="M88 54 L96 68 L102 66 L96 52 Z" />
          <circle cx="58" cy="48" r="2.5" fill="rgba(5,12,24,0.35)" />
        </g>

        {/* Maritime patrol aircraft — upper mid-left */}
        <g transform="translate(250 175) rotate(4)" fill={inkDim}>
          <ellipse cx="70" cy="42" rx="68" ry="11" />
          <path d="M8 42 L-18 26 L-8 26 L28 38 L28 46 L-8 58 L-18 58 Z" />
          <path d="M110 36 L148 24 L156 30 L122 42 Z" />
          <path d="M110 48 L148 60 L156 54 L122 42 Z" />
          <path d="M130 38 L152 34 L158 38 L152 46 L130 46 Z" />
          <rect x="52" y="28" width="5" height="8" rx="0.8" />
          <circle cx="42" cy="42" r="2" fill="rgba(5,12,24,0.3)" />
        </g>

        {/* Cruise missile — mid flight, center-right */}
        <g transform="translate(620 280) rotate(-18)" fill={inkSoft}>
          <path d="M0 14 L110 10 L128 14 L110 18 L0 18 Z" />
          <path d="M18 10 L8 0 L16 2 L28 10 Z" />
          <path d="M18 18 L8 28 L16 26 L28 18 Z" />
          <path d="M96 10 L108 4 L114 8 L104 14 Z" />
          <path d="M96 18 L108 24 L114 20 L104 14 Z" />
          <circle cx="122" cy="14" r="3.5" />
        </g>

        {/* Attack submarine — mid-left waterline */}
        <g transform="translate(100 430)" fill={inkSoft}>
          <path d="M16 44 C40 28, 90 22, 160 26 C210 30, 250 38, 268 46 L252 56 C210 50, 150 52, 80 54 C48 54, 24 50, 16 44 Z" />
          <rect x="118" y="14" width="4" height="18" rx="0.8" />
          <path d="M108 30 L140 30 L134 38 L114 38 Z" />
          <circle cx="200" cy="40" r="2.2" fill="rgba(5,12,24,0.25)" />
        </g>
      </svg>
    </div>
  );
}
