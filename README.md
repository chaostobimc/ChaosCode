# ChaosCode

**ChaosCode** is a terminal coding agent: it can inspect a project, search files, propose edits, and run commands after you approve them. It uses the unofficial [`xtekky/deepseek4free`](https://github.com/xtekky/deepseek4free) approach to talk to DeepSeek's free web chat; it does **not** use the paid DeepSeek API or ask for an API key.

> **Important:** `deepseek4free` uses DeepSeek's undocumented web interface and requires a DeepSeek web-account token. This is not an official DeepSeek API or an uptime guarantee; the interface may change, rate-limit requests, or block unofficial clients. Use it in accordance with DeepSeek's terms. ChaosCode does not attempt to bypass CAPTCHA or Cloudflare protections. Do not use an account token you are not authorized to use.

## Install the `chaoscode` command

Install once from a checkout. **Both `uv` and `pipx` work**: they create an isolated environment and add the `chaoscode` console command to your user PATH. You do **not** have to run a Python file to start ChaosCode.

### Windows (PowerShell) with `uv`

Install `uv` once, for example with WinGet:

```powershell
winget install --id=astral-sh.uv -e
```

Close and reopen PowerShell, then install Python 3.12 through uv and install ChaosCode from its checkout:

```powershell
uv python install 3.12
cd C:\Pfad\zu\ChaosCode
uv tool install --python 3.12 .
uv tool update-shell
```

Open a new terminal if prompted, then check `chaoscode --help`. The tool environment is isolated; the `chaoscode` command is added to your user PATH.

### Windows alternative: `pipx`

Install Python 3.10 or newer first (the example uses Python 3.12), then install pipx once and run this from the ChaosCode checkout:

```powershell
py -3.12 -m pip install --user pipx
py -3.12 -m pipx ensurepath
pipx install .
```

Restart PowerShell if `ensurepath` asks you to. `pipx` and `uv` both install the same `chaoscode` command; you only need to choose one.

### Linux / macOS

With `uv`, install it using your platform's package manager, then from the checkout run `uv tool install .` (and `uv tool update-shell` if the tool directory is not on PATH). With `pipx`, install pipx once and run:

```bash
# Install pipx once (choose the option for your system):
# Debian/Ubuntu: sudo apt install pipx
# macOS/Homebrew: brew install pipx
# Other systems:  python3 -m pip install --user pipx
pipx ensurepath

# From the ChaosCode checkout:
pipx install .
```

To update after changing the checkout, run `uv tool install --force .` or `pipx install --force .` from the repository root. Both create a persistent launcher; `uv tool run` / `uvx` is temporary and is not the system-wide install command.

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

# Load a project file into the model context with @path
chaoscode "Review @src/main.py and fix the input validation"

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

Inside an interactive session, use `/help`, `/clear`, `/new`, `/status`, or `/exit`. Conversations are saved locally under the ChaosCode user config directory (with owner-only file permissions); use `--no-save` if you do not want the transcript persisted. Project instructions in a root-level `CHAOSCODE.md`, `AGENTS.md`, or `CLAUDE.md` are included in its working context.

### Select files with `@path`

In the interactive prompt, type `@` to see available project files and folders, then use the arrow keys and **Tab** to complete a suggestion. You can also type a path directly. A project-relative mention loads the file as model context:

```text
Please review @src/main.py and fix the input validation.
```

Use `@"folder/file name.py"` or `@{folder/file name.py}` for paths containing spaces. You can select up to four files per task. ChaosCode applies its normal project-root and secret-file protections; large file context is shortened, and the agent can use `read_file` for more. The file stays editable through the usual tools, and every change still requires your approval. Interactive terminals show a colored ChaosCode banner and lightly styled Markdown; `NO_COLOR=1` disables colors, and `--print` stays plain for scripts.

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
