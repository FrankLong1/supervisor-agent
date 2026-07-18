# Shared inbox directory

| Human/workstation | Agent | Immutable agent ID | Runtime database user |
| --- | --- | --- | --- |
| Alice (`alice@`) | `research@alice` | `7a3fa6fa-2f49-42c9-bb6a-d4a9eafed720` | `demo-agent-inbox-alice@gv-data-platform.iam` |
| Frank (`frank@`), whose agent is Bob | `helper@bob` | `fa212e75-7581-457b-a918-4ac8bc617bbc` | `demo-agent-inbox-bob@gv-data-platform.iam` |

Canonical routing:

- `alice` or `alice@` always means `research@alice`.
- `frank`, `frank@`, or `bob` always means `helper@bob`.
- Alice may send only to Frank/Bob; Frank/Bob may send only to Alice.

The workstation login is human ownership metadata. PostgreSQL authenticates the
separate keyless runtime service-account identity through Cloud SQL Auth Proxy.
The database derives the sender principal from `session_user` and verifies that
`SUPERVISOR_INBOX_AGENT_ID` belongs to it.

Sending creates a `TASK_PROPOSAL`. It does not mean the recipient accepted the
task. Misaddressed, self-addressed, or unknown principals must fail before any
database call.
