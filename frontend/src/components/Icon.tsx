import type { SVGProps } from "react";

export type IconName =
  | "dashboard"
  | "task"
  | "proposal"
  | "hazard"
  | "site"
  | "estop"
  | "logs"
  | "arrow"
  | "shield";

const paths: Record<IconName, JSX.Element> = {
  dashboard: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
  task: <><path d="M12 5v14M5 12h14" /><circle cx="12" cy="12" r="9" /></>,
  proposal: <><path d="M9 18h6M10 22h4" /><path d="M8.2 15.4A7 7 0 1 1 15.8 15.4C14.7 16.2 14 17 14 18h-4c0-1-.7-1.8-1.8-2.6Z" /></>,
  hazard: <><path d="m12 3 9 17H3L12 3Z" /><path d="M12 9v4M12 17h.01" /></>,
  site: <><path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3V6Z" /><path d="M9 3v15M15 6v15" /></>,
  estop: <><path d="M7.7 3h8.6L21 7.7v8.6L16.3 21H7.7L3 16.3V7.7L7.7 3Z" /><path d="M9 9h6v6H9z" /></>,
  logs: <><path d="M6 3h12v18H6z" /><path d="M9 8h6M9 12h6M9 16h4" /></>,
  arrow: <><path d="M5 12h14M13 6l6 6-6 6" /></>,
  shield: <><path d="M12 3 4.5 6v5.2c0 4.7 3.2 8.1 7.5 9.8 4.3-1.7 7.5-5.1 7.5-9.8V6L12 3Z" /><path d="m9 12 2 2 4-5" /></>,
};

export function Icon({ name, ...props }: { name: IconName } & SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      {paths[name]}
    </svg>
  );
}
