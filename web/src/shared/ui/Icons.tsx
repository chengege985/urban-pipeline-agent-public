import type { SVGProps } from "react";
import type { ServiceType } from "../contracts/domain";

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function base({ size = 18, ...props }: IconProps) {
  return {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
    ...props,
  };
}

export function LogoIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M12 3.2c3.6 4.1 6.2 7 6.2 10.1a6.2 6.2 0 0 1-12.4 0C5.8 10.2 8.4 7.3 12 3.2Z" />
      <path d="M8.4 13.1c.7 1.5 2 2.3 3.6 2.3 1.1 0 2.1-.4 2.8-1.1" />
      <circle cx="12" cy="13.4" r="1" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function OverviewIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <rect x="3.5" y="3.5" width="7" height="7" rx="1.5" />
      <rect x="13.5" y="3.5" width="7" height="7" rx="1.5" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1.5" />
      <rect x="13.5" y="13.5" width="7" height="7" rx="1.5" />
    </svg>
  );
}

export function PipeIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M4 8.5h7.5a3 3 0 0 1 3 3V20" />
      <path d="M4 5.5v6" />
      <path d="M14.5 4h6" />
      <path d="M18 4v6.5a3 3 0 0 1-3 3h-2" />
      <circle cx="4" cy="8.5" r="1.5" />
      <circle cx="14.5" cy="20" r="1.5" />
    </svg>
  );
}

export function ScenarioIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="5" cy="6" r="2" />
      <circle cx="19" cy="6" r="2" />
      <circle cx="12" cy="18" r="2" />
      <path d="M7 6h9.5M6.5 8l4.4 8M17.5 8l-4.4 8" />
    </svg>
  );
}

export function AssistantIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M5 5.5h14a1.5 1.5 0 0 1 1.5 1.5v8a1.5 1.5 0 0 1-1.5 1.5H9l-4 3v-3H5A1.5 1.5 0 0 1 3.5 15V7A1.5 1.5 0 0 1 5 5.5Z" />
      <path d="M8 10h8M8 13h5" />
    </svg>
  );
}

export function InfoIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 10.8v5.4M12 7.6h.01" />
    </svg>
  );
}

export function ArrowRightIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M5 12h13M13 6l6 6-6 6" />
    </svg>
  );
}

export function ResetIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M4.5 10a7.5 7.5 0 1 1 1.8 7.5" />
      <path d="M4.5 4.5V10H10" />
    </svg>
  );
}

export function SearchIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <circle cx="10.5" cy="10.5" r="6" />
      <path d="m15 15 4.5 4.5" />
    </svg>
  );
}

export function AlertIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M12 4 21 20H3L12 4Z" />
      <path d="M12 9v5M12 17h.01" />
    </svg>
  );
}

export function CheckIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="m5 12 4.2 4.2L19 6.5" />
    </svg>
  );
}

export function CloseIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="m6 6 12 12M18 6 6 18" />
    </svg>
  );
}

export function HospitalIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <rect x="5" y="4" width="14" height="17" rx="2" />
      <path d="M12 8v7M8.5 11.5h7M9 21v-2.5h6V21" />
    </svg>
  );
}

export function SchoolIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M3.5 9.5 12 5l8.5 4.5L12 14 3.5 9.5Z" />
      <path d="M6.5 11v5.2c0 1.5 2.5 2.8 5.5 2.8s5.5-1.3 5.5-2.8V11" />
      <path d="M20.5 9.5v5" />
    </svg>
  );
}

export function MallIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="M4 9.5h16l-1.2 10.5H5.2L4 9.5Z" />
      <path d="M8 9.5V7a4 4 0 0 1 8 0v2.5M9 21v-5h6v5" />
    </svg>
  );
}

export function HomeIcon(props: IconProps) {
  return (
    <svg {...base(props)}>
      <path d="m3.5 11 8.5-7 8.5 7" />
      <path d="M5.5 10v10h13V10M9.5 20v-6h5v6" />
    </svg>
  );
}

export function ServiceIcon({ type, ...props }: IconProps & { type: ServiceType }) {
  if (type === "医院") return <HospitalIcon {...props} />;
  if (type === "学校") return <SchoolIcon {...props} />;
  if (type === "商业") return <MallIcon {...props} />;
  if (type === "未知") return <InfoIcon {...props} />;
  return <HomeIcon {...props} />;
}
