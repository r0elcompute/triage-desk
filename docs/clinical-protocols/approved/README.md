# Approved Protocol References

This directory is the only production filesystem-MCP mount. Do not add clinical documents until their source, version, license/reuse terms, clinical authorization, and checksum have been verified and approved through project governance.

`registry.json` intentionally contains no approved clinical documents. The project has not yet verified and approved the WHO IITT source materials or implemented an IITT evaluator. Consequently, production workflow runs that request a clinical protocol fail closed and create a clinician review request; they do not evaluate using an unapproved or substituted protocol.

Add an approved document only with a matching registry entry containing its identifier, version, approval status, safe relative filename, SHA-256, source URL, and license/reuse statement. The runtime mounts this directory read-only into the official Filesystem MCP server.
