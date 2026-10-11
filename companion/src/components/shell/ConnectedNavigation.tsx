"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { Ref } from "react";
import NavIcon, { type NavIconName } from "./NavIcon";
import styles from "./Shell.module.scss";

const items: {
  href: string;
  label: string;
  icon: NavIconName;
  matches: (path: string) => boolean;
}[] = [
  { href: "/", label: "Today", icon: "today", matches: (p) => p === "/" },
  {
    href: "/ops",
    label: "Classes",
    icon: "classes",
    matches: (p) => p.startsWith("/ops"),
  },
  {
    href: "/people",
    label: "Members",
    icon: "members",
    matches: (p) => p.startsWith("/people") || p === "/integration/members",
  },
  {
    href: "/follow-ups",
    label: "Follow-ups",
    icon: "followups",
    matches: (p) => p === "/follow-ups" || p === "/hub",
  },
];
export default function ConnectedNavigation({
  mobile = false,
  onCompanion,
  companionOpen,
  triggerRef,
}: {
  mobile?: boolean;
  onCompanion?: () => void;
  companionOpen?: boolean;
  triggerRef?: Ref<HTMLButtonElement>;
}) {
  const pathname = usePathname();
  return (
    <nav
      className={mobile ? styles.connectedBottomNav : styles.connectedNav}
      aria-label={mobile ? "Mobile staff navigation" : "Staff navigation"}
    >
      {!mobile && (
        <Link href="/" className={styles.wordmark} aria-label="Dojang home">
          <span className={styles.brandMark}>
            D<span>.</span>
          </span>
          <span>
            DOJANG<small>COMPANION</small>
          </span>
        </Link>
      )}
      {items.map((item) => (
        <Link
          key={item.href}
          href={item.href}
          aria-current={item.matches(pathname) ? "page" : undefined}
        >
          <NavIcon name={item.icon} />
          <span>{item.label}</span>
        </Link>
      ))}
      {onCompanion && (
        <button
          ref={triggerRef}
          onClick={onCompanion}
          aria-label="Open companion"
          aria-controls="roster-companion"
          aria-expanded={companionOpen}
        >
          <NavIcon name="companion" />
          <span>Companion</span>
        </button>
      )}
      {!mobile && (
        <Link href="/kiosk" className={styles.kioskLink}>
          Open check-in kiosk <span aria-hidden="true">↗</span>
        </Link>
      )}
    </nav>
  );
}
