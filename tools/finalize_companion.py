"""Small idempotent integration hooks, preserving the existing kiosk screens."""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1] / "companion"

def transform(path, marker, replacements, append=""):
    file=ROOT/path
    source=file.read_text()
    if marker in source: return
    for before,after in replacements:
        if source.count(before)!=1: raise RuntimeError("Unexpected source marker: "+path)
        source=source.replace(before,after,1)
    file.write_text(source+append)

transform("src/integrations/kiosk.ts", "// CONNECTED_ATTENDANCE_RECOVERY", [
    ('export async function checkIn(', 'async function sendCheckIn('),
    ('// The only place kiosk screens get data from.', 'import { saveUnconfirmedAttendance, clearConfirmedAttendance } from "./attendanceRecovery";\n\n// The only place kiosk screens get data from.'),
], '''

// CONNECTED_ATTENDANCE_RECOVERY
// Reconcile the ORIGINAL command after uncertainty. Saved is not confirmed.
export async function checkIn(command: CheckInCommand, signal: AbortSignal): Promise<CheckInAnswer> {
  const answer = await sendCheckIn(command, signal);
  if (answer.kind === "result") await clearConfirmedAttendance(command);
  else if (answer.kind === "noAnswer") await saveUnconfirmedAttendance(command);
  return answer;
}
''')
transform("app/(kiosk)/kiosk/page.tsx", 'import RecoveryStatus', [
    ('import IdleWarning from', 'import RecoveryStatus from "@/components/kiosk/recovery/RecoveryStatus";\nimport IdleWarning from'),
    ('<IdleWarning secondsLeft={idle.secondsLeft} onStillHere={idle.stillHere} />', '<IdleWarning secondsLeft={idle.secondsLeft} onStillHere={idle.stillHere} />\n      <RecoveryStatus />'),
])
transform("src/domain/kiosk/attendanceOutbox.ts", '// SAME_KEY_PAYLOAD_CHECK', [
    ('if (state.entries.some(e => e.id === id && e.scope === context.scope)) return true;', '''// SAME_KEY_PAYLOAD_CHECK
    const existing = state.entries.find(e => e.id === id && e.scope === context.scope);
    if (existing) {
      try {
        const clear = await this.crypt.subtle.decrypt({name: "AES-GCM", iv: new Uint8Array(existing.iv)}, await this.cryptoKey(context), new Uint8Array(existing.ciphertext));
        const saved = JSON.parse(new TextDecoder().decode(clear));
        return validQueuedCommand(saved) && saved.payload.memberId === command.payload.memberId && saved.payload.sessionId === command.payload.sessionId && saved.expectedVersion === command.expectedVersion;
      } catch { return false; }
    }'''),
])
print("Reviewed recovery hooks applied. No remote writes performed by this script.")
