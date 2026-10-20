# Private Statement Reader

<!-- impeccable:product-schema 1 -->

## Platform

web

The document interface runs in WKWebView inside a native AppKit macOS application. The native shell owns the window, menus, file dialogs and local engine lifecycle.

## Users

The user processes approximately 300 pages of scanned Indian bank statements on an Apple Silicon Mac.

## Product Purpose

Import PDFs together and automatically produce searchable PDFs, extracted transactions, running-balance checks and CSV/XLSX files. Show uncertain readings with the scanned source above the extracted values.

## Operating Context

Private local desktop use on the current 10-core, 16 GB Mac. The user chooses a best-effort memory budget, 1–8 simultaneous documents (hardware permitting), OCR CPU parallelism and up to two local GLM-OCR 8-bit model processes. Balanced settings are the default. Live changes apply at safe operation boundaries; memory pressure reduces concurrency. Original documents and audit history remain local.

## Capabilities and Constraints

No paid APIs, subscriptions or cloud processing. Local Tesseract Best, Apple Vision, GLM-OCR through MLX, OCRmyPDF and image processing. Automatic resume, bounded retries, duplicate-file detection and combined exports. Estimated amounts and names retain provenance. Arithmetic agreement does not certify source accuracy. Optional corrections are available; approvals and manual layouts are not prerequisites for automatic completion.

## Brand Commitments

The user explicitly requests the appearance and conventions of a native Mac app. Use macOS interface conventions and native controls where the shell can own them. Preserve the source-first uncertainty view.

## Evidence on Hand

Synthetic scan and extraction tests live in tests/. The user's real statements are stored privately in Application Support and must not be packaged with application or source deliverables.

## Product Principles

- Import once; the batch runs automatically.
- Preserve source evidence and audit history.
- Expose uncertainty without presenting guesses as verified facts.
- Use the laptop's resources within memory-pressure limits.
- Follow macOS navigation, typography, controls and keyboard conventions.
