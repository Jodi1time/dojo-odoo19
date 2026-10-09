"use client";

import { ReactNode, useState } from "react";
import Rail from "./rail/Rail";
import ContextPane from "./ContextPane";
import styles from "./Shell.module.scss";
import WorkspaceHeader from "./workspace/WorkspaceHeader";
import WorkspaceTabs from "./workspace/WorkspaceTabs";
import CompanionRail from "./rail/companion-rail/CompanionRail";
import AdaptiveBottomNav from "./bottom-nav/AdaptiveBottomNav";

type Props = {children: ReactNode; context?: ReactNode; companion?: ReactNode; connectedTest?: boolean};

export default function AdaptiveShell({children, context, companion, connectedTest = false}: Props) {
  const [contextOpen, setContextOpen] = useState(false);
  const [companionOpen, setCompanionOpen] = useState(false);
  return (
    <div className={styles.adaptiveShell} data-context-open={contextOpen} data-companion-open={companionOpen}>
      <div className={styles.railSlot}>
        {connectedTest ? <nav aria-label="Connected test navigation"><a href="/integration/members">Test members</a></nav> : <Rail />}
      </div>
      <div className={styles.contextSlot}>
        <button className={styles.contextClose} onClick={() => setContextOpen(false)} aria-label="Close context panel">✕</button>
        {context !== undefined ? context : <ContextPane>{undefined}</ContextPane>}
      </div>
      <div className={styles.workspaceSlot}>
        {connectedTest ? <header><h1>Dojang connected test</h1><p>Authorized synthetic data. Unconnected features are disabled.</p></header> : <>
          <WorkspaceHeader onToggleContext={() => setContextOpen(open => !open)} contextOpen={contextOpen} />
          <WorkspaceTabs />
        </>}
        <div className={styles.workspaceBody}>{children}</div>
      </div>
      <div className={styles.companionSlot}>
        <button className={styles.contextClose} onClick={() => setCompanionOpen(false)} aria-label="Close companion">✕</button>
        {companion !== undefined ? companion : <CompanionRail />}
      </div>
      <button className={styles.companionTrigger} onClick={() => setCompanionOpen(true)} aria-label="Open companion">✦</button>
      {!connectedTest && <div className={styles.bottomNavSlot}><AdaptiveBottomNav /></div>}
    </div>
  );
}
