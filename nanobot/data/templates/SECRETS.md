# SECRETS.md — Available Docker Secrets

This file documents which Docker secrets are available to you.

## Security Rules

1. **NEVER read secret files directly** — don't `cat` them to see the content
2. **NEVER pass secrets as command arguments** — they'll appear in logs
3. **ALWAYS pipe secrets** — use `cat /run/secrets/<name> | command`

## Available Secrets

<!-- List secrets that the harness has mounted -->

| Secret Name | Path | Purpose |
|-------------|------|---------|
| `keepassxc_passphrase` | `/run/secrets/keepassxc_passphrase` | Unlocks the KeePassXC database |

## Usage Patterns

### KeePassXC — Read a password entry

```bash
cat /run/secrets/keepassxc_passphrase | keepassxc-cli show -s /path/to/database.kdbx "entry-name" -a password
```

### KeePassXC — List all entries

```bash
cat /run/secrets/keepassxc_passphrase | keepassxc-cli ls /path/to/database.kdbx
```

### General pattern for any command

```bash
cat /run/secrets/<secret-name> | command-that-needs-secret --password-stdin
```

## Why This Matters

- Secrets passed as arguments appear in `ps`, logs, and shell history
- Secrets piped via stdin stay invisible
- You know *which* secrets exist, but never see *what's in them*
- This is defense in depth — even if logs leak, secrets don't
