# Publishing Jarvix V.01 to GitHub

This project is ready to publish as `Jarvix V.01` / `v0.1.0`.

## Option 1: Create the Repo on GitHub.com

1. Go to GitHub and create a new empty repository named `Jarvix`.
2. Do not add a README, license, or `.gitignore` on GitHub because this project
   already includes them.
3. From this local project folder, run:

```bash
cd /Users/zain/Documents/Jarvix
git remote add origin https://github.com/zyadd1111111/Jarvix.git
git push -u origin main
git push origin v0.1.0
```

## Option 2: Use GitHub CLI

Install and authenticate GitHub CLI first:

```bash
brew install gh
gh auth login
```

Then create the repo and push:

```bash
cd /Users/zain/Documents/Jarvix
gh repo create zyadd1111111/Jarvix --private --source=. --remote=origin --push
git push origin v0.1.0
```

Use `--public` instead of `--private` if you want the repository to be public.

## Release Details

- Release title: `Jarvix V.01`
- Tag: `v0.1.0`
- Release notes file: `RELEASE_NOTES_V0.1.0.md`

## Quick Verification Before Pushing

```bash
cd /Users/zain/Documents/Jarvix
source .venv/bin/activate
PYTHONPATH=src python -m pytest -q
git status -sb
```
