# Jarvix extension SDK v1

Extensions are bounded declarative JSON manifests that compose registered Jarvix capabilities. No Python, JavaScript, shell, remote module or executable plugin code is loaded. New native implementations still require a reviewed host adapter; v1 is a composition SDK, not an arbitrary-code sandbox.

Use `examples/plugins/daily_focus/jarvix-plugin.json` as a starting point. Import it using `plugins.install` from an approved file root, then review `plugins.preview` and approve its exact fingerprint with `plugins.enable`. The manager is under **Knowledge → Extensions**. An edited manifest needs approval again.

Required fields: `id` (directory-safe lowercase identifier), `name`, semantic `version`, `sdk_version: 1`, `permissions` and `capabilities`. The authoritative JSON schema is `MANIFEST_SCHEMA` in `capabilities/plugins.py`.

Each declared contribution has a `name`, `target` and optional `title`:

| Capability | Host behavior |
| --- | --- |
| tools | Register `plugin.ID.NAME` with the target schema and permissions. |
| services / integrations / actions | Call the declared host endpoint through `plugins.invoke`. Integrations reuse connected host account adapters and their scopes. |
| panels | Open the existing permissioned tool form from the extension manager. |
| commands | Add an action to the command palette. |
| triggers | Add a named preset for an existing workflow event; saved workflows retain the reviewed native trigger and configuration. |

Every target permission must be declared. Targets cannot load plugins or call other plugin tools. Core permissions, file roots, access switches, cancellation and fresh sensitive-action confirmation remain mandatory. Scheduled execution does not gain a blanket extension grant. A damaged or modified extension fails independently and its contributions become unavailable. Previously approved unchanged manifests restore after restart.

After enabling tool aliases, restart Jarvix to refresh AI catalog families and the Settings capability checklist. Tool forms and command contributions can be used immediately.
