# Rehearsed local browser demo

Use the materialized `release/monday-demo` branch. Run `bash tools/monday/start_demo.sh` on Linux/WSL with Docker and Node 22. The script creates a fresh temporary copy of the committed source, builds pinned Odoo, runs the native regression suite, creates only synthetic fixtures, and builds the frontend. It does not alter an existing Odoo database or the current checkout's environment files.

Open the printed localhost pairing page in two separate browser profiles. Pair one as kiosk and one as staff using the private key files written in the temporary directory. Credentials are not printed, committed, or included in evidence archives. Keep the terminal running for the demonstration. Stopping the script stops only its generated containers. Re-running starts a new clean synthetic demonstration.

Walkthrough:
1. Kiosk: Check in, select Demo session, search Avery, select the matched student, confirm. Verify the Odoo receipt.
2. Staff: Open Avery in Member 360. Verify one check-in and the same class/time.
3. Companion: Read the actual attendance summary, prepare an internal follow-up task, review it, approve. Verify the Odoo task receipt. The mode is explicitly guided/rules-based, not generative AI.
4. Kiosk: select Blake, disconnect network after loading the roster, confirm. It must say pending sync, not checked in. Reconnect with the kiosk open. Staff Member 360 must show exactly one new attendance.
5. Verify Done/idle reset clears the attendee. A repeated submission must not create another attendance record.

The GitHub rehearsal runs this same application code against an isolated Odoo/PostgreSQL database and saves screenshots and direct database counts. Read its exact `materialized-commit.txt` and `release-gate.txt` before calling that revision tested.

A shared hosted URL is a separate requirement. The current Vercel operation was rejected with 403 for the supplied Dojang team/project scope. This script is a local presentation path, not a claim that hosted deployment is complete. Electron/map composition and an authenticated generative-AI provider are not included in this browser test slice.
