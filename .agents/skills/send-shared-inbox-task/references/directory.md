# Shared inbox directory

| Human/workstation | Google runtime principal |
| --- | --- |
| Alice (`alice@`) | `alice@gravitationalventures.com` |
| Frank (`frank@`) | `frank@gravitationalventures.com` |

Canonical routing:

- `alice`, `alice@`, or Alice's full Google email means Alice.
- `frank`, `frank@`, or Frank's full Google email means Frank.
- Alice may send only to Frank; Frank may send only to Alice.

Agent addresses and immutable UUIDs are deployment outputs, not human login
names. Configure all four before sending:

- `SUPERVISOR_INBOX_ALICE_AGENT_ADDRESS`
- `SUPERVISOR_INBOX_ALICE_AGENT_ID`
- `SUPERVISOR_INBOX_FRANK_AGENT_ADDRESS`
- `SUPERVISOR_INBOX_FRANK_AGENT_ID`

The script also requires the sender's normal `SUPERVISOR_INBOX_AGENT_ADDRESS`
and `SUPERVISOR_INBOX_AGENT_ID` to exactly match one configured pair. Missing,
duplicate, or partial mappings fail before the supervisor or database is called.

The workstation login is human ownership metadata. PostgreSQL authenticates the
actual Google user through Cloud SQL IAM authentication. A separate keyless
migrator service account owns schema changes and is not a runtime sender. The
database derives the sender principal from `session_user` and verifies that
`SUPERVISOR_INBOX_AGENT_ID` belongs to it.

Sending creates a `TASK_PROPOSAL`. It does not mean the recipient accepted the
task. Misaddressed, self-addressed, or unknown principals must fail before any
database call.
