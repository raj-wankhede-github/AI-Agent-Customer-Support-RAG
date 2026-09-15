export function LogoMark({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="8" fill="#4f46e5" />
      <path
        d="M9 10.5A2.5 2.5 0 0 1 11.5 8h9A2.5 2.5 0 0 1 23 10.5v7a2.5 2.5 0 0 1-2.5 2.5H15l-4.2 3.4c-.5.4-1.3 0-1.3-.6V20H11.5A2.5 2.5 0 0 1 9 17.5z"
        fill="#fff"
      />
      <path d="m12.8 14 2.2 2.2 4.2-4.4" fill="none" stroke="#4f46e5" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Logo({ name = "Acme Support", inverted = false }: { name?: string; inverted?: boolean }) {
  return (
    <span className="flex items-center gap-2.5">
      <LogoMark />
      <span className={`text-[15px] font-semibold tracking-tight ${inverted ? "text-white" : "text-slate-900"}`}>{name}</span>
    </span>
  );
}
