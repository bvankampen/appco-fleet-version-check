# Git Workflow and Pushing Rules for AI Agents

To ensure the safety, integrity, and human control over the codebase and its remote repository, all AI agents operating in this workspace must strictly adhere to the following rule:

## ⚠️ PUSHING RESTRICTION

* **NEVER push to the remote Git repository automatically.**
* Pushing to any remote branch (e.g., `git push`) is **STRICTLY PROHIBITED** unless the user has **explicitly and unambiguously instructed you to push** in their prompt.
* If the user asks you to "commit", "create a PR", or "prepare changes", you must perform the local staging and commits, but **STOP** before pushing, and provide the user with instructions or ask for explicit confirmation to proceed with pushing.
