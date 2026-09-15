import type { SVGProps } from "react";

// A small, consistent 24px stroke icon set (currentColor). Decorative by default; pass
// aria-label and role="img" when an icon conveys meaning on its own.
const PATHS = {
  send: "M4 12 20 4l-4 16-4-7-8-1Zm8 1 8-9",
  attachment: "m21 11.5-8.6 8.6a5.5 5.5 0 0 1-7.8-7.8l8.6-8.6a3.7 3.7 0 0 1 5.2 5.2l-8.6 8.6a1.8 1.8 0 0 1-2.6-2.6l7.9-7.9",
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm10 2-4.3-4.3",
  settings:
    "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7.4-3a7.4 7.4 0 0 0-.1-1.3l2-1.6-2-3.4-2.4 1a7.5 7.5 0 0 0-2.2-1.3L14.3 3h-4l-.4 2.4a7.5 7.5 0 0 0-2.2 1.3l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 0 0 0 2.6l-2 1.6 2 3.4 2.4-1a7.5 7.5 0 0 0 2.2 1.3l.4 2.4h4l.4-2.4a7.5 7.5 0 0 0 2.2-1.3l2.4 1 2-3.4-2-1.6c.1-.4.1-.9.1-1.3Z",
  dashboard: "M3 3h8v8H3V3Zm10 0h8v5h-8V3ZM3 13h8v8H3v-8Zm10-3h8v11h-8V10Z",
  document: "M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6Zm0 0v6h6M8 13h8M8 17h5",
  conversation: "M21 12a8 8 0 0 1-11.8 7L4 20.5l1.5-4.7A8 8 0 1 1 21 12Z",
  handoff:
    "M4 14v-2a8 8 0 0 1 16 0v2M4 14a2 2 0 0 0 2 2h1v-5H6a2 2 0 0 0-2 2v1Zm16 0a2 2 0 0 1-2 2h-1v-5h1a2 2 0 0 1 2 2v1Zm-3 2v1a4 4 0 0 1-4 4h-1",
  thumbUp: "M7 11v10H4V11h3Zm0 0 4-8a2.5 2.5 0 0 1 2.5 2.5V9h5.2a2 2 0 0 1 2 2.3l-1.3 8A2 2 0 0 1 17.4 21H7",
  thumbDown: "M17 13V3h3v10h-3Zm0 0-4 8a2.5 2.5 0 0 1-2.5-2.5V15H5.3a2 2 0 0 1-2-2.3l1.3-8A2 2 0 0 1 6.6 3H17",
  menu: "M4 6h16M4 12h16M4 18h16",
  close: "M6 6l12 12M18 6 6 18",
  plus: "M12 5v14M5 12h14",
  chevronDown: "m6 9 6 6 6-6",
  chevronRight: "m9 6 6 6-6 6",
  chevronLeft: "m15 6-6 6 6 6",
  upload: "M12 16V4m0 0-4 4m4-4 4 4M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3",
  refresh: "M20 11a8 8 0 0 0-14.9-3.9L4 9m0-5v5h5M4 13a8 8 0 0 0 14.9 3.9L20 15m0 5v-5h-5",
  trash: "M4 7h16M10 11v6m4-6v6M6 7l1 13a2 2 0 0 0 2 1.9h6a2 2 0 0 0 2-1.9L18 7M9 7V4h6v3",
  check: "m5 12 5 5L20 7",
  alert: "M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z",
  info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-5v-4m0-4h.01",
  logout: "M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 17l5-5-5-5M15 12H3",
  shield: "M12 21s8-3.5 8-10V5l-8-3-8 3v6c0 6.5 8 10 8 10Zm-3-9 2 2 4-4",
  bot: "M12 3v3m-6 3h12a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2Zm3 5h.01M15 14h.01M9.5 17.5h5",
  user: "M20 21a8 8 0 0 0-16 0m8-10a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13v4l3 2",
  book: "M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5v14Zm0 0A2.5 2.5 0 0 0 6.5 22H20v-5",
  power: "M12 3v8m6.4-4.4a8 8 0 1 1-12.8 0",
  bug: "M8 8V6a4 4 0 0 1 8 0v2m-8 0h8a2 2 0 0 1 2 2v4a6 6 0 0 1-12 0v-4a2 2 0 0 1 2-2Zm-4 5H2m20 0h-2M4 8l2 2m14-2-2 2M4 20l2-2m14 2-2-2M12 12v6",
  arrowLeft: "M19 12H5m0 0 6-6m-6 6 6 6",
  external: "M14 4h6v6m0-6-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5",
  lock: "M6 11h12a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1Zm2 0V7a4 4 0 0 1 8 0v4",
  sparkle: "M12 3v4m0 10v4M3 12h4m10 0h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 20, strokeWidth = 1.75, ...props }: SVGProps<SVGSVGElement> & { name: IconName; size?: number }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={props["aria-label"] ? undefined : true}
      focusable="false"
      {...props}
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
