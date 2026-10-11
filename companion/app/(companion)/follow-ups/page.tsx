import Link from "@/components/primitives/AppLink";
import { odooTestMode } from "@/server/odoo-runtime";
import ConnectedWorkflowView from "@/components/ai/follow-up/ConnectedWorkflowView";
import CompanionRail from "@/components/shell/rail/companion-rail/CompanionRail";
import styles from "@/components/workspace/Workspace.module.scss";
export default function FollowUpsPage() {
  if (odooTestMode())
    return (
      <>
        <ConnectedWorkflowView historyOnly />
        <div className={styles.page}>
          <Link className={styles.quietButton} href="/hub">
            Open staff Hub →
          </Link>
        </div>
      </>
    );
  return (
    <section className={`${styles.page} ${styles.followups}`}>
      <header className={styles.heading}>
        <div>
          <div className={styles.eyebrow}>
            A little attention goes a long way
          </div>
          <h1>Follow through.</h1>
          <p>
            Review sample tasks with Companion. Demo approvals don’t send real
            messages.
          </p>
        </div>
      </header>
      <div>
        <CompanionRail />
      </div>
    </section>
  );
}
