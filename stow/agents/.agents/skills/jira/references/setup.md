# Jira CLI setup

## Install

The dotfiles Brewfile declares `atlassian/acli/acli` from Atlassian's official
tap. Setup trusts that exact formula. Homebrew owns upgrades; do not install
a second copy elsewhere on PATH.

For an explicitly authorized installation on an existing machine:

```bash
brew trust --formula atlassian/acli/acli
brew tap atlassian/acli
brew install atlassian/acli/acli
acli --version
```

If Homebrew rejects the selected Xcode version, report the prerequisite
failure. Do not change the global Xcode selection automatically; other
projects and running builds may depend on it. Homebrew filters
`DEVELOPER_DIR` from its command environment, so a per-command override is
not a reliable remedy.

## Authenticate

The user runs this command in their terminal:

```bash
acli jira auth login --web
```

They approve browser consent and select the same site in the terminal.
Never ask them to paste an OAuth URL, authorization code, or token into chat.
Authentication is a user-authorized setup step, not an automatic response to
every failed read. If organization policy blocks consent, stop and report it.

After the user confirms completion, check the active account and site:

```bash
acli jira auth status
```

Keep CLI-owned account state out of git and Stow. Do not migrate the older
MCP clients' credentials or copy live configuration. The CLI also supports
API-token login, but changing authentication methods needs a separate
decision and a reviewed credential-input path.

## Verify one agreed story

Ask for one issue key or URL the user authorizes reading in this Pi session.
Retrieve only `key,issuetype,summary,status,description` with the skill's
command. Confirm the returned key and report the retrieved summary plus
whether the description is present. Do not include the description in the
setup report or fetch comments, attachments, custom fields, or related
issues as part of this first check.

A successful login or help command does not prove Jira retrieval works.
Leave live verification pending until this read succeeds. Normal story
tasks can then use the skill's scoped commands.

Reload Pi with `/reload` after adding the skill. Invoke `/skill:jira`
explicitly if automatic routing does not select it.

## Maintenance evidence

The recipes were checked against official documentation and the help output
of Atlassian CLI `1.3.36-stable`. The downloaded Apple Silicon archive matched
the official tap's SHA-256:

```text
f5307d1518364c20f35f92c745bfc1282a41b4cd4a4c83dfdf4e50d4e0ca3945
```

Live verification covered browser OAuth and one user-selected issue read.
The returned key matched, the summary was present, and the description was
a JSON object. Search, comments, and custom-field retrieval remain untested
against the live site.

These checks are not a source-code audit or proof of credential-storage
security or absence of read-history side effects.

Atlassian documents a six-month support window for CLI releases. Recheck
recipes against local help and official docs after upgrades. Keep real
account details, site URLs, issue identifiers, and custom-field mappings
out of this shared skill.

## Official references

- [Documentation index](https://developer.atlassian.com/cloud/acli/).
- [macOS installation](https://developer.atlassian.com/cloud/acli/guides/install-macos/).
- [Authentication](https://developer.atlassian.com/cloud/acli/guides/how-to-get-started/).
- [View a work item](https://developer.atlassian.com/cloud/acli/reference/commands/jira-workitem-view/).
- [Search with JQL](https://developer.atlassian.com/cloud/acli/reference/commands/jira-workitem-search/).
- [List comments](https://developer.atlassian.com/cloud/acli/reference/commands/jira-workitem-comment-list/).
- [Official Homebrew formula](https://github.com/atlassian/homebrew-acli/blob/main/Formula/acli.rb).
