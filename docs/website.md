# Hosting `marclevin.me/gonk`

The goal: these two commands work forever, whatever happens inside
GonkLander.

```bash
curl -fsSL https://marclevin.me/gonk | bash
```

```powershell
irm https://marclevin.me/gonk.ps1 | iex
```

## How it fits together

```text
marclevin.me/gonk          a 40-line shim, in the website repository. Never changes.
        │ downloads
        ▼
raw.githubusercontent.com/marclevin/GonkLander/<version>/lander/install.sh
        │ installs uv, then
        ▼
github.com/marclevin/GonkLander/archive/<version>.tar.gz     the gonk package
```

GitHub Pages serves static files and cannot redirect, so the stable URL has
to serve *something*. It serves a shim whose only job is to fetch the real
installer. The website repository holds two small files and knows nothing
else about Gonk. GonkLander can be restructured, rewritten or moved without
touching the website, as long as `lander/install.sh` stays where it is.

## Setting it up

One-time, in the website repository.

1. **Make GonkLander public.** The bootstrap carries no credentials, so it
   can only download from a repository that needs none.

   ```bash
   cd ~/Code/GonkLander
   gh repo create marclevin/GonkLander --public --source . --push
   ```

2. **Copy the two shims** to the root of the published site.

   ```bash
   cp ~/Code/GonkLander/lander/shim/gonk      <website>/gonk
   cp ~/Code/GonkLander/lander/shim/gonk.ps1  <website>/gonk.ps1
   ```

   "Root of the published site" depends on how the site is built:

   | Site | Put the files in |
   | --- | --- |
   | Plain HTML, or Jekyll | the repository root |
   | Hugo | `static/` |
   | Astro, Vite, Next.js, Eleventy | `public/` |

   With Jekyll, check that `_config.yml` does not `exclude` them. A file
   with no front matter is copied as it is.

3. **Commit, push, and check.**

   ```bash
   curl -fsSL https://marclevin.me/gonk | head -5      # should print the shim
   curl -fsSL https://marclevin.me/gonk | bash         # should install gonk
   ```

That is all. There is nothing to deploy when Gonk changes.

## Choosing what gets installed

By default the shim installs the `main` branch. Anyone running it can choose:

```bash
curl -fsSL https://marclevin.me/gonk | GONK_VERSION=v0.1.0 bash
curl -fsSL https://marclevin.me/gonk | GONK_PROFILE=dev bash     # install, then land
```

Both the installer and the package come from the same version, so a pinned
install is pinned all the way through.

### Releasing

```bash
# bump the version in pyproject.toml and src/gonk/__init__.py, then
git tag v0.1.0
git push origin main v0.1.0
```

### Making the default safer than `main`

Installing `main` means every push is live on the next machine. When that
becomes uncomfortable, create a `stable` branch, change one line in the
website's copy of the shim, and never touch it again:

```bash
local version="${GONK_VERSION:-stable}"
```

Then releasing is `git push origin main:stable`, and `main` is free to be
broken.

## Things to know

**Content type.** GitHub Pages serves a file with no extension as
`application/octet-stream`. `curl … | bash` does not care. For the
PowerShell shim this has **not been verified**: if `irm` returns bytes
instead of text, serve the shim from a path that Pages gives a text type,
or use the long form:

```powershell
iex (New-Object Net.WebClient).DownloadString('https://marclevin.me/gonk.ps1')
```

**A page called `gonk`.** If the site ever gets a page at `/gonk/`, it will
collide with the file `/gonk`. Keep the name for the installer.

**Trust.** Anyone who can push to the website repository or to GonkLander
controls what that command runs. Protect both with two-factor
authentication, and see [security.md](security.md) for what is not yet
verified.

**Without the website.** The shim is a convenience. This always works:

```bash
curl -fsSL https://raw.githubusercontent.com/marclevin/GonkLander/main/lander/install.sh | bash
```

## Alternative: copy the installer instead of a shim

The website could serve `install.sh` itself, copied over by a GitHub Action
on each GonkLander release. That saves one download and removes the
dependency on `raw.githubusercontent.com`, but it couples the two
repositories: a release has to push to the website, which needs a token with
write access stored as a secret. The shim needs neither, which is why it is
the recommendation.
