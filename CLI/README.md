# CLI Services

This folder keeps third-party CLI setup scripts for Linux servers (the only supported deployment target).

## OpenAI Codex CLI

- Install/update: `CLI/linux/openai/install_openai_codex_cli.sh`

After installation, open a new terminal and run:

```bash
codex
```

The first run prompts you to sign in with a ChatGPT account or an API key.

## Gemini CLI

Install via npm on the server:

```bash
npm install -g @google/gemini-cli
gemini
```

The first run prompts you to sign in with your Google account or configure Gemini authentication.

## Jimeng (Dreamina) CLI

Install natively on Linux:

```bash
curl -fsSL https://jimeng.jianying.com/cli | bash
dreamina --help
```

The app locates these binaries via `PATH` (`shutil.which`), so make sure they are on the PATH of the user running the service.
