"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { getSessions } from "@/integrations/kiosk";
import type { SessionOption } from "@/contracts/kiosk-attendance";
import styles from "./Workspace.module.scss";
const time = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
export default function TodayView({
  classesOnly = false,
  demo = false,
}: {
  classesOnly?: boolean;
  demo?: boolean;
}) {
  const [sessions, setSessions] = useState<SessionOption[] | null>(null);
  const [error, setError] = useState(false);
  const [now, setNow] = useState<number | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    getSessions(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) {
          setSessions(
            [...data].sort(
              (a, b) => Date.parse(a.startsAt) - Date.parse(b.startsAt),
            ),
          );
          setError(false);
          setNow(Date.now());
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      });
    return () => controller.abort();
  }, [revision]);
  useEffect(() => {
    const refresh = () => setRevision((n) => n + 1);
    window.addEventListener("focus", refresh);
    const timer = setInterval(refresh, 60000);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", refresh);
    };
  }, []);
  const next = sessions?.find((s) => Date.parse(s.endsAt) > (now ?? 0));
  const started = next && Date.parse(next.startsAt) <= (now ?? 0);
  const schedule = (
    <ul className={styles.schedule}>
      {sessions?.map((s) => (
        <li key={s.sessionId}>
          <Link href={`/ops/sessions/${s.sessionId}`}>
            <time dateTime={s.startsAt}>{time(s.startsAt)}</time>
            <div>
              <h3>{s.title}</h3>
              <p>
                {s.seatsTaken} / {s.capacity} booked · Until {time(s.endsAt)}
              </p>
            </div>
            <span className={styles.arrow} aria-hidden="true">
              ↗
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
  return (
    <section className={styles.page}>
      <header className={styles.heading}>
        <div>
          <div className={styles.eyebrow}>
            {classesOnly ? "On the mat" : "Your day at Dojang"}
          </div>
          <h1>
            {classesOnly ? "Today’s classes." : "A strong day starts here."}
          </h1>
          <p>
            {classesOnly
              ? "Every class. Every student. One place to get ready."
              : "A clear view of what’s next, with your people at the center."}
          </p>
        </div>
        <Link className={styles.quietButton} href="/kiosk">
          Open check-in <span aria-hidden="true">↗</span>
        </Link>
      </header>
      {error ? (
        <div className={styles.empty} role="alert">
          <h2>Classes aren’t available yet.</h2>
          <p>Connect an authorized staff device or try loading again.</p>
          <div>
            <Link className={styles.button} href="/integration/pair">
              Connect device
            </Link>{" "}
            <button
              className={styles.quietButton}
              onClick={() => setRevision((n) => n + 1)}
            >
              Try again
            </button>
          </div>
        </div>
      ) : sessions === null ? (
        <p role="status">Loading your day…</p>
      ) : (
        <>
          {!classesOnly && (
            <>
              <div className={styles.hero}>
                <div>
                  <div className={styles.pill}>
                    {next
                      ? started
                        ? "On the mat now"
                        : "Up next"
                      : "Your workspace"}
                  </div>
                  <h2>{next?.title ?? "Ready for what comes next."}</h2>
                  <p>
                    {next
                      ? `${next.seatsTaken} students booked · ${next.capacity} class capacity`
                      : "Today’s schedule is complete. Your class rosters and member records are still here."}
                  </p>
                  <Link
                    className={styles.button}
                    href={next ? `/ops/sessions/${next.sessionId}` : "/people"}
                  >
                    {next ? "View class roster" : "Explore members"}{" "}
                    <span aria-hidden="true">→</span>
                  </Link>
                </div>
                {next && (
                  <div className={styles.heroTime}>
                    {time(next.startsAt)}
                    <span>Until {time(next.endsAt)}</span>
                  </div>
                )}
              </div>
              <div className={styles.stats}>
                <article>
                  <p>Classes today</p>
                  <strong>{sessions.length}</strong>
                  <small>On today’s schedule</small>
                </article>
                <article>
                  <p>Class bookings</p>
                  <strong>
                    {sessions.reduce((n, s) => n + s.seatsTaken, 0)}
                  </strong>
                  <small>Bookings across classes</small>
                </article>
                <article>
                  <p>Places available</p>
                  <strong>
                    {sessions.reduce(
                      (n, s) => n + Math.max(0, s.capacity - s.seatsTaken),
                      0,
                    )}
                  </strong>
                  <small>Across today’s classes</small>
                </article>
              </div>
            </>
          )}
          <div className={classesOnly ? undefined : styles.columns}>
            <section>
              <div className={styles.sectionHeading}>
                <h2>
                  {classesOnly ? "Class schedule" : "The rest of your day"}
                </h2>
                {!classesOnly && <Link href="/ops">All classes →</Link>}
              </div>
              {sessions.length ? (
                schedule
              ) : (
                <div className={styles.empty}>
                  No classes are scheduled today.
                </div>
              )}
            </section>
            {!classesOnly && (
              <aside className={styles.card}>
                <div className={styles.eyebrow}>People before paperwork</div>
                <h2>Make the next conversation count.</h2>
                <p>
                  Open a member to see their attendance, review their progress,
                  and prepare a thoughtful follow-up.
                </p>
                <Link className={styles.quietButton} href="/people">
                  Find a member →
                </Link>
                <p className={styles.note}>
                  {demo
                    ? "Sample records are shared with the app’s demo check-in flow. No real messages are sent."
                    : "Your device permissions determine which records are available."}
                </p>
              </aside>
            )}
          </div>
        </>
      )}
    </section>
  );
}
