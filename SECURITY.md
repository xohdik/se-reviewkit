# Security and data handling

SE-ReviewKit is a local research CLI, not a hosted service. Runtime dependencies are from the Python standard library. Keep your Python installation patched and review provider access terms before making requests.

- Use environment variables or hidden prompts for API credentials; never hard-code them in configurations, commands or source control.
- Authenticated retrieval attaches credentials only to the initial request, not redirected requests, and rejects raw responses that echo credential material.
- Keep raw licensed exports, unpublished manuscripts, selection frames, reviewer packets and results in a private workspace. The `.gitignore` rules are a safety net, not a security boundary: inspect staged changes before pushing.
- Raw metadata may be untrusted. Treat CSV text cells as text in spreadsheet software; never enable source-supplied formulas. Do not execute downloaded artifacts without sandboxing and review.
- Verify input and output checksums; preserve immutable retrieval and adjudication checkpoints. Do not overwrite completed audit outputs.
- If you find a vulnerability or accidentally exposed credential, revoke the credential and contact the repository maintainer privately via an established contact channel. Do not publish secrets or restricted evidence in a GitHub issue.

This release has offline tests but no third-party security certification. The project does not claim live provider availability or scientific validation based on software tests.
