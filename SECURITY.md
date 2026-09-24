# Security and privacy boundaries

Jarvix is a local application with opt-in cloud AI. It is not a sandbox for untrusted Python plugins or approved terminal commands.

- No credentials are saved to SQLite or text settings. Supported OS vault backends are explicitly allowlisted. Environment keys can be supplied for development and take precedence over vault keys.
- Cloud requests contain bounded current-conversation history, a system policy and the enabled tool schemas. Context inspection is available in Chat. No unrelated notes, memories, files, activity or credentials are automatically added.
- Tool arguments use closed JSON schemas. Level 1 reads are immediate, Level 2 uses normal control or one-time consent, and Level 3 always asks immediately before execution. Stored grants never authorize Level 3. Execution and cloud disclosure are independent decisions.
- Tool output is untrusted content. Prompt instructions reinforce this boundary; concrete host permissions enforce it. The model cannot bypass denied permissions or create new capabilities.
- App launches use registered executables. Configured arguments require fresh confirmation of the exact executable/argv on every launch. Developer commands use structured argv with shell disabled, explicit working directories, time/output limits and mandatory confirmation. An approved program can access resources outside Jarvix's allowed roots: roots are not an OS sandbox. Browser tools do not scrape sites or acquire authenticated browser state.
- File tools enforce allowed roots, reject links/junctions and protected filenames, validate archive entries and avoid overwriting user files. Undo verifies recorded changes. Recycling has no permanent-delete fallback. These checks cannot defend against an unrelated hostile process racing filesystem mutations. Indexing stores metadata, not document content.
- Audit entries record action names and outcomes, not argument bodies or local tool data. Conversations contain the text the user intentionally sends and final model answers.
- Tool loops, payloads, output sizes, model rounds and response sizes are bounded. Reused call IDs are stopped to prevent repeat execution. Providers do not automatically retry requests.
- SQLite is **not encrypted**. Files inherit the operating system's user-profile access controls on Windows. Full-disk encryption and account security are the user's storage boundary. Close Jarvix before backing up the data directory; include SQLite WAL files if copying a live database.
- The Settings backup action uses SQLite's snapshot API and an integrity check to create a self-contained local database copy while the app remains open. Backups exclude vault credentials and stay under the active profile's `backups` folder.
- Scheduled routines use a fixed safe-local allowlist: system/task/project summaries and local notifications. They never call cloud models, launch apps or browsers, alter files, or fetch private content. They run only while the app is open.
- Manual routines check every nested action's permission. Clipboard, screen and microphone have explicit switches; none is a passive context source. Dictation is local and temporary audio is removed. Wake-word listening is not implemented.
- Cancellation takes effect at operation boundaries. An in-flight HTTP request can run until its timeout. Completed side effects cannot be undone by Stop.

Before public distribution: code-sign desktop binaries, review dependency licenses and redistribution notices (including Qt/PySide6), maintain dependency/security updates, test on each supported OS and add a tested upgrade/backup procedure. Network-account connectors need scoped OAuth and separate consent before becoming available.

Report issues privately to the project maintainer before publishing reproduction data containing local records or credentials. Do not attach your SQLite database, API keys or unredacted conversation history to public reports.
