# BugBridge

English · [简体中文](README.zh-CN.md)

BugBridge helps a coding agent reproduce reported problems, compare upstream
issues/PRs, and prepare issue or comment drafts for you to review and copy.

## Quick start

1. **Clone the workspace.**

   ```bash
   git clone https://github.com/Lui-hhub/BugBridge.git
   cd BugBridge
   ```

2. **Open the directory in a coding agent that reads [AGENTS.md](AGENTS.md).**
   Give it a **materials path** and a **target library/project name**, for example:

   > My materials are at /path/to/materials, and the target library is pytorch.
   > Follow this workspace's AGENTS.md, prepare the issue materials, reproduce the
   > claims, compare existing issues/PRs, and give me the local review URL and expiry time.

   Scripts, templates and the review page are bundled in this checkout. No global
   skill installation is required. Use an existing Python 3.10+ interpreter;
   YAML template validation needs an existing interpreter with PyYAML. Reproduction
   uses a compatible existing environment for the target library. GitHub searches
   prefer a locally configured personal access token (PAT). The agent asks if an
   environment or authentication is missing; keep tokens in local configuration.

3. **Open the returned localhost URL.**
   Review a report, follow its official issue link, and copy the prepared fields.
   You decide what to submit and publish it yourself. In WSL, the URL can be opened
   in Windows Edge.

   Each review service closes automatically **3 hours after startup**; page visits
   do not extend it. The agent provides the expiry time with the URL. Files and copy
   progress are preserved; ask the agent to reopen the review when needed.

Task artifacts are saved under `runs/`; selection and copy progress are saved in
`.local/state/review.sqlite3`. Original materials stay unchanged. For multiple
libraries, provide one materials path per library; each gets a separate run and
review URL, while GitHub requests share the machine-level queue.

## More

- [Full workflow](SKILL.md)
- [Review service and saved progress](references/service.md)
- [Contributing and checks](CONTRIBUTING.md)

## License

BugBridge's original code and documentation are [MIT licensed](LICENSE).
Bundled upstream templates retain their own licenses and attribution; see
[third-party notices](THIRD_PARTY_NOTICES.md).
