# Security policy

Do not put a security report with private data in a public issue.
Use the repository's private vulnerability reporting option if it is available.
If it is not available, open an issue that requests a private contact method.
Do not include exploit details or statement data in that issue.

This project is an early-stage local application. It has no security audit certification.
Only the current default branch receives fixes.

## Scope

The local service uses a random loopback port and a session token.
It rejects unauthorized requests and cross-origin writes.
The service is not designed for public network access or multiple users.
Do not expose its port through a proxy or tunnel.

The app does not encrypt its data files.
Use macOS account controls, FileVault and appropriate backup protection.
OCR text, previews, exports and logs can contain financial data.

Installation can download packages and public model files.
Document processing does not require a cloud OCR service.
Dependencies and model files retain their own security risks and licenses.
