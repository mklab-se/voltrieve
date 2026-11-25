/** Small drawings. The text beside them, or a label, says what they mean. */
import type { ReactNode } from "react";

const icon = (children: ReactNode) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {children}
  </svg>
);

export const WiperIcon = () =>
  icon(
    <>
      <path d="M3 9c5.2-4.6 12.8-4.6 18 0l-3.3 8.2c-3.5-2-7.9-2-11.4 0z" />
      <path d="M12 19 15.5 9.6" />
    </>,
  );

export const SpeakerIcon = ({ on }: { on: boolean }) =>
  icon(
    <>
      <path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5z" />
      {on ? <path d="M15.5 9a4.5 4.5 0 0 1 0 6M18 6.5a8 8 0 0 1 0 11" /> : <path d="m16 9.5 5 5m0-5-5 5" />}
    </>,
  );

export const SendIcon = () => icon(<path d="M5 12h13M12.5 6.5 18 12l-5.5 5.5" />);

export const ArrowIcon = ({ up }: { up: boolean }) => icon(up ? <path d="m6 14.5 6-6 6 6" /> : <path d="m6 9.5 6 6 6-6" />);

export const TurnIcon = ({ left }: { left: boolean }) => (
  <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d={left ? "M11 4 2 12l9 8v-5h11V9H11z" : "m13 4 9 8-9 8v-5H2V9h11z"} />
  </svg>
);

export const CloseIcon = () => icon(<path d="m6 6 12 12M18 6 6 18" />);

export const MenuIcon = () => (
  <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <circle cx="5" cy="12" r="1.7" />
    <circle cx="12" cy="12" r="1.7" />
    <circle cx="19" cy="12" r="1.7" />
  </svg>
);

export const ParkingSign = () => (
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <rect x="2" y="2" width="20" height="20" rx="4" fill="#1d5fa8" stroke="#fff" strokeOpacity="0.85" />
    <text x="12" y="17.5" textAnchor="middle" fontSize="16" fontWeight="700" fill="#fff">
      P
    </text>
  </svg>
);
