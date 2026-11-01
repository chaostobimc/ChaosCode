# ChaosCode

**ChaosCode** is a terminal coding agent: it can inspect a project, search files, propose edits, and run commands after you approve them. It uses the unofficial [`xtekky/deepseek4free`](https://github.com/xtekky/deepseek4free) approach to talk to DeepSeek's free web chat; it does **not** use the paid DeepSeek API or ask for an API key.

> **Important:** `deepseek4free` uses DeepSeek's undocumented web interface and requires a DeepSeek web-account token. This is not an official DeepSeek API or an uptime guarantee; the interface may change, rate-limit requests, or block unofficial clients. Use it in accordance with DeepSeek's terms. ChaosCode does not attempt to bypass CAPTCHA or Cloudflare protections. Do not use an account token you are not authorized to use.

## Install the `chaoscode` command

Install once from a checkout. `pipx` creates the `chaoscode` terminal command and puts it on your PATH; you do **not** have to run a Python file to start ChaosCode.

### Linux / macOS

```bash
# Install pipx once (choose the option for your system):
# Debian/Ubuntu: sudo apt install pipx
# macOS/Homebrew: brew install pipx
# Other systems:  python3 -m pip install --user pipx
pipx ensurepath

# From the ChaosCode checkout:
pipx install .
```

Open a new terminal if `ensurepath` asks you to. Then run:

```bash
chaoscode --help
```

### Windows (PowerShell)

```powershell
# From the ChaosCode checkout
py -m pip install --user pipx
py -m pipx ensurepath
pipx install .
```

Open a new terminal if needed, then run `chaoscode --help`. Python is used by the installed launcher behind the scenes, but you start the app with the `chaoscode` command like any other CLI tool.

To update after changing the checkout, run `pipx install --force .` from the repository root. A normal `pip install .` also installs the command, but `pipx` is recommended to keep ChaosCode's dependencies isolated from other Python tools.

## Connect DeepSeek

1. Sign in to [chat.deepseek.com](https://chat.deepseek.com/) in your browser.
2. In the browser developer tools, open **Application/Storage → Local Storage → `https://chat.deepseek.com`** and locate `userToken`. Copy the nested token value. The upstream project also documents this Console expression: `JSON.parse(localStorage.getItem("userToken")).value`.
3. In a terminal, run `chaoscode auth login` and paste the token at the hidden prompt. The token is saved under your user config directory with restrictive permissions (POSIX: owner-only; Windows: under your user `AppData`). It is never printed back or stored in the project.

You can alternatively set `DEEPSEEK_AUTH_TOKEN` in your environment. Environment variables take precedence over the saved token. `chaoscode auth status` reports where a token is configured without revealing it; `chaoscode auth logout` removes the saved token.

**Treat this token like a password.** Do not paste it into chat, commit it, or put it in a project file. If it is exposed, revoke/sign out of the web session.

## Use it

```bash
# Start an interactive coding session in the current directory
chaoscode

# Give it a task directly
chaoscode "Find and fix the off-by-one bug in the pagination code"

# Search can be enabled for DeepSeek responses (off by default)
chaoscode --search "Check the current package docs and update this project"

# Continue the latest saved session for this project
chaoscode --resume

# One-shot / script-friendly mode. Tool actions are denied unless --yes is explicit.
chaoscode --print "Summarize this repository"
chaoscode --print --yes "Run the tests and fix failures"

# Other commands
chaoscode doctor
chaoscode sessions list
chaoscode auth status
```

Inside an interactive session, use `/help`, `/clear`, `/new`, `/status`, or `/exit`. Conversations are saved locally under the ChaosCode user config directory (with owner-only file permissions); use `--no-save` if you do not want the transcript persisted. The project files are read only when needed by the agent. Project instructions in a root-level `CHAOSCODE.md`, `AGENTS.md`, or `CLAUDE.md` are included in its working context.

## Safety and tools

The agent has tools to list, search, and read files; edit or create a file; and run a shell command. **Every write and shell command is shown for approval first.** Use `--yes` only when you intentionally want to auto-approve these actions. Commands run with your user permissions, in the selected project directory. File tools are confined to that directory; common secret files such as `.env`, private keys, and `.git` internals are not readable through the file tools. Review shell commands before approving them.

Prompts, selected source files, and tool output are sent to DeepSeek's web service for inference. Do not use ChaosCode with code or data you are not allowed to share with that service. Local conversation history may also contain project snippets you asked the agent to inspect.

## Development

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
chaoscode --help
```

The vendored proof-of-work helper is derived from [`xtekky/deepseek4free`](https://github.com/xtekky/deepseek4free), upstream commit `4ae47bb` (MIT); its license is included at `chaoscode/_vendor/deepseek4free/LICENSE`. ChaosCode's adapter intentionally does not bundle that project's browser automation / Cloudflare bypass scripts.
