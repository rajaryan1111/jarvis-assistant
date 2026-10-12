# Credential exposure and rotation guide

This document records which credentials were exposed in this repository and what the owner must do
manually. It deliberately contains **no secret values**. Never paste keys into issues, pull requests,
chats or commits.

## Summary

| Credential | Provider | Where it was exposed | Status in the working tree | Owner action |
|---|---|---|---|---|
| Picovoice AccessKey | Picovoice | `wakeword_listener.py` (hardcoded, line 12) on `main` up to and including commit `35b2a33` | **Removed** in M1. Now read from `PICOVOICE_ACCESS_KEY` | **Rotate now** |
| NewsAPI key | NewsAPI | `news.py` and `news_test.py` in commits before `a3bdbd2` | Removed earlier (`a3bdbd2`). Read from `NEWS_API_KEY` | **Rotate** if not already done |

The repository is **public**, so treat both values as compromised. Removing them from the current
files does **not** remove them from Git history, forks, clones or caches. History has **not** been
rewritten, and nothing was force-pushed (this was a deliberate project constraint).

No credentials were revoked through provider APIs, and no provider accounts were changed. Rotation is a
manual owner action.

## 1. Rotate the Picovoice AccessKey

1. Sign in to the Picovoice Console (<https://console.picovoice.ai/>) with the account that owns the key.
2. In the AccessKey section, regenerate or reset the key. This invalidates the old one.
   Picovoice ties usage to the account's AccessKey, so anyone holding the old key could use your quota
   until it is reset.
3. Put the new key only in your local `.env`:
   ```env
   PICOVOICE_ACCESS_KEY=<new key>
   ```
4. Check it without printing it: `uv run ultron doctor` should show `PICOVOICE_ACCESS_KEY: configured`.
5. Check the console's usage page for activity you don't recognise since the key was first committed.

## 2. Rotate the NewsAPI key

1. Sign in to your NewsAPI account (<https://newsapi.org/account>).
2. If the account page offers to regenerate the key, do it. If it doesn't, contact NewsAPI support to
   have the exposed key revoked and replaced. The exposed key stays valid until then.
3. Store the new key in `.env` as `NEWS_API_KEY=<new key>` and confirm with `uv run ultron doctor`.

## 3. Check the other keys

The Phase 0 audit found only the two keys above in tracked files and history. If `OPENAI_API_KEY`,
`WEATHER_API_KEY` or `CRICKET_API_KEY` were ever shared outside your machine (screenshots, chats,
copied `.env` files), rotate them too:

- OpenAI: <https://platform.openai.com/api-keys>. Create a new key, then delete the old one.
- OpenWeather: <https://home.openweathermap.org/api_keys>. Generate a new key, then delete the old one.
- CricketData: rotate the key in your account dashboard.

## 4. Rotate the local ULTRON API token if needed

The local API token is generated on your machine and never committed. Rotate it if you think it was
copied somewhere:

```bash
uv run ultron token rotate    # invalidates every HUD session immediately after a server restart
```

## 5. Historical exposure (owner decision, not done in M1)

The old values stay retrievable from public Git history until history is rewritten **and** the
key is rotated. Rotation is what actually protects you; rewriting history alone does not, because
copies may already exist.

If you also want to purge the values from history later, the usual tools are `git filter-repo` or the
BFG Repo-Cleaner, followed by a coordinated force-push and a request to GitHub Support to clear cached
views. That rewrites every commit hash and breaks existing clones and forks, so do it deliberately
and only after rotating. It was intentionally **not** done as part of M1.

GitHub secret scanning alerts for this public repository, if any, appear under **Security → Secret
scanning**. Close them only after rotating.

## 6. Preventing recurrence

- Credentials are read only from the environment or a local `.env` (`ultron/config.py`). `.env`,
  `.env.*`, token files, key files, local databases, memory and face data are git-ignored.
- `uv run ultron check-secrets` scans every file that could be committed and prints only file, line and
  rule (never the value). CI runs it on every push, and `tests/test_secret_scan.py` fails if a hardcoded
  credential appears in the repository.
- For extra protection, enable GitHub push protection for secret scanning in the repository settings.
