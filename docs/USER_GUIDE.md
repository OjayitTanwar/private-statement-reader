# Private Statement Reader for Mac

Free local bank statement OCR, automatic table extraction, AI-assisted recovery, running-balance verification, and Excel/CSV exports. The native Apple Silicon app opens its own Mac window, with native PDF picker and Save dialogs. It does not require a browser account, subscription, API key, or paid service.

## Open the app

Open **outputs/Private Statement Reader.app**. It can also be dragged to Applications. On this Mac the free OCR dependencies are already installed. If missing on a different Mac, run **outputs/Setup.command** once; Homebrew must already be installed from https://brew.sh. Installation requires Internet; document processing does not.

Requires Apple Silicon, macOS 14 or later, Homebrew Python 3, Tesseract, OCRmyPDF, and Poppler. The app bundles its Python application code, Pillow, and openpyxl, but **does not bundle the Python interpreter or Homebrew OCR programs**. It is built and ad-hoc signed for local use, not Apple-notarized for public distribution. On another Mac, macOS may require right-click → Open or an allowance in Privacy & Security. No Apple developer subscription is needed to build or use it locally.

## Workflow

The Mac window uses a native compact toolbar, SF Symbols, system menus and PDF picker, with a Mac-styled document workspace. The grouped sidebar separates processing, queued and completed statements. Choose View → Appearance for system, light or dark appearance. Use ⌘O to import PDFs, ⌘F to search, ⌘E to open finished files, and ⌥⌘S to show or hide the sidebar. Source screenshots remain above uncertain readings in either appearance.

1. Import bank statement PDFs together. The app chooses concurrent document workers from CPU cores and RAM: this 10-core, 16 GB Mac can run four documents together, with two searchable-PDF workers per document. A shared pool loads up to two copies of GLM-OCR 8-bit for concurrent inference while OCR for other documents overlaps. Both copies use the same GPU; a proportional speedup is not guaranteed. Memory pressure reduces AI admission to one instance and releases the idle second copy. macOS memory pressure also limits new document work to two documents at warning level or one at critical level; documents already running continue. Closing the window keeps processing in the background; click the Dock icon to reopen it. Explicit Quit stops processing; the next launch resumes automatically from completed pages. The app prevents idle sleep while work is active.
2. Each PDF gets a separate original, searchable copy, full-page previews, OCR evidence, transaction crops, and editable rows. OCRmyPDF failure is shown explicitly; an unsearchable original is never labeled searchable.
3. Extraction, retries, cell recognition, local AI proposals, and balance checks run automatically. No row approval or column setup is required to export. Optional review is available beside the source scan. Correct dates, normalized descriptions, cheque/reference numbers, debit, credit and balance; the original OCR description stays unchanged. Approval is independent from arithmetic: a wrong balance stays flagged even if approved.
4. Opening balances and transaction order are detected automatically. Uncertain pages get a focused enhanced-image AI recovery attempt. Missing values can be estimated from neighboring balance equations and stay labeled as inferred. Optional source inspection and overrides are available, but are not prerequisites for completion. The app uses integer paise for reconciliation.
5. The automatic process reports omissions and unresolved estimates; optional full-page inspection and corrections remain available. Add a missing row after the selected row on the current page. Exclude false positives without deleting their evidence. Re-extraction archives a snapshot of existing edits in Review history.
6. CSV, XLSX, a diagnostic report and the searchable PDF are saved automatically under **Show Finished Files**. The top-level **All statements.csv** and **All statements.xlsx** combine completed bank statements and retain each source filename, document ID and page. Each statement also has its own subfolder. Excluded rows are omitted; uncertainties remain visible in the files. Optional corrections refresh saved outputs. Export buttons provide extra copies if desired.

The app shows **Uncertain readings** above the optional correction tools. Each item has its source crop at the top, followed by extracted values, earlier OCR readings where available, and field-specific labels for estimates, unreadable values, conflicting readings and guessed names. Balance mismatches appear first. Six items load at a time, with navigation through the remaining issues. Arithmetic agreement and manual confirmation are shown separately; neither hides outstanding balance differences. These checks do not stop automatic extraction or saving.

Completed pages have durable checkpoints. A failed document is retried automatically once, then a failure report is saved and the batch continues. A local AI timeout retires that worker so a delayed response cannot be attached to the wrong page. Identical page/model inputs reuse cached AI readings. Pages that explicitly say there are no transactions skip expensive table recovery and cannot create account-summary transactions.

Identical PDFs already imported are reused. Pending duplicate copies in an older library are excluded from the processing queue while their source files remain stored. This prevents duplicate work and duplicate totals in the automatically combined export.

## OCR and bank specialization

Thorough mode is the default. It compares:

- **Tesseract 5**, using table-oriented segmentation and positioned word confidence.
- **Apple Vision**, using `.accurate` recognition with language correction disabled to preserve names and identifiers. This Apple system framework is free on macOS but is not open source; the primary Tesseract/PDF pipeline is open source and works without Vision.
- A second **Tesseract** pass on a contrast-enhanced, sharpened image with different segmentation.
- The bundled **tessdata_best English** model rather than a small speed-focused model.
- **Automatic bank-table detection**: separately read inverted dark headers, optimize residual skew against the actual ruling lines, find table contours and cell boundaries with image morphology, and OCR small padded cell batches to prevent column merging.
- A **whole-statement candidate decoder** keeps multiple observed number readings alive, then scores the sequence against balance equations. Page opening balances reset separate statement segments. It does not assume that every PDF page is one uninterrupted ledger.
- A local **GLM-OCR 8-bit vision-language model** supplies additional visual readings and description/name guesses. The AI model and its runtime are bundled when installed during the build; inference stays offline. This runtime build uses Homebrew Python 3.13 and MLX 0.32.4 (macOS 26+); ordinary OCR can run on older supported macOS releases.

Each page is rendered up to 4,500 pixels on its longest side. OCR words are regrouped geometrically so separate OCR blocks do not destroy table rows. Header synonyms include withdrawal/debit, deposit/credit, particulars/narration, cheque/reference, and balance. Detected column centers carry over to following pages; unfamiliar tables can be calibrated manually by percentage of page width. Wrapped narration is retained when it aligns with the description column.

The strongest structural candidate is retained. Disagreements or missing matches flag a row. With AI-assisted automatic recovery, unknown amounts can be estimated from the balance delta; an isolated wrong balance can be proposed from two adjacent equations when both imply the same value. Such values are explicitly labeled as **inferred**, with original readings and methods retained. They are not claimed to be independently printed evidence. Raw OCR geometry, AI proposals, and cell evidence stay local. Confidence is an OCR quality score, **not a measured probability that the transaction is correct**. Agreement and reconciliation are useful evidence, not proof: missing transactions or matching errors can still pass arithmetic.

Bank-statement recognition requires account/statement evidence plus a monetary table with balance, a structured AI reading of financial transactions, or an explicit empty account summary. Unrecognized documents receive an automatic diagnostic report and an **unsupported** status. No manual column calibration is required to finish the batch. This is a heuristic gate, not a trained document classifier or bank-certified parser.

For each chronological row:

```text
previous printed balance − debit + credit = current printed balance
```

A single amount with an ambiguous direction can be assigned when an exact balance delta supports it during extraction. Missing opening balance, missing amount/balance, two populated transaction columns, weak OCR, mismatches and OCR disagreement are flagged. AI and arithmetic estimates are allowed, as requested, but remain labeled rather than silently presented as exact OCR. A gap in readable balances breaks verification continuity. Exports include inference status, original OCR values, AI proposals, and inferred-field provenance.

## Name review

Counterparty guesses are extracted automatically from explicit transfer, NEFT/RTGS and UPI narrations. Repeated clear source spellings can repair narrow OCR character confusions, with the observed spelling and method retained. Similar-sounding distinct names are not silently merged. No people list is required. These are text readings and guesses, not verified identities.

Open **People list**, enter known people one per line, and optional spellings separated by `|`:

```text
Ajay Sharma | Ajai Sharma
Mohammed Khan | Mohammad Khan
```

The app offers possible spelling and English phonetic matches to the description. A reviewer chooses a match and saves it in a separate counterparty field. These are not identity checks. It does not search the Internet or read Contacts; unrelated people can have similar names. Non-Latin phonetic matching is not supported in this version. Original text remains available for comparison.

## Privacy and storage

The app service binds only to a random port on `127.0.0.1` with a random session cookie. Cross-origin writes and unauthorized requests are rejected. There are no analytics, CDN assets, uploads, outbound OCR calls, or cloud accounts. Data is written with owner-only default permissions under:

```text
~/Library/Application Support/Private Statement Reader/
```

Use the app menu → **Show Local Data** to view/back up documents and the SQLite database. Finished files are in its **exports/** subfolder. Data is not application-encrypted; FileVault and macOS account protections apply. The source PDF is preserved byte-for-byte. Corrections are audit logged. Names and financial text can appear in exports and local OCR evidence. Each concurrent document holds one page image at a time, and documents share a pool of at most two AI model copies. CPU/RAM limits choose 1–4 document workers. Disk usage grows with retained page previews and OCR evidence; plan several GB for hundreds of pages.

## Development and rebuilding

```sh
brew install python tesseract ocrmypdf poppler
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python build_mac.py
```

Building requires Xcode Command Line Tools (`xcode-select --install`). Build output is in `outputs/`. The build uses `swiftc` and a local ad-hoc code signature, with no paid build service. The copied Pillow binaries should be built with the same Homebrew Python version the app will use; rebuild after a Python major/minor upgrade.

For development without the Mac wrapper, run `.venv/bin/python app.py` and open the unique session URL printed to the terminal. Set `STATEMENT_VISION` to the built `Contents/Resources/VisionOCR` helper to enable Vision. Development data defaults to `data/`. Legacy prototype data remains untouched; the new implementation uses separate `bank_*` tables.

## Verification and honest limits

```sh
python3 -m unittest discover -s tests -v
python3 tests/smoke_local.py
```

The smoke test creates a clearly labeled synthetic two-page Indian statement (including a faint page) and runs actual OCR, searchable-PDF generation, HTTP review, name suggestions, exports, corrections, audit, and reconciliation. Its data is under `work/smoke-data`, not your real app library.

A 13-page real statement imported through the Mac app was used to identify problems with tilted dark headers, malformed number separators, B/F balances and disconnected statement periods. This is diagnostic testing, **not an independently labeled accuracy benchmark**. The requested 300-page collection has not been benchmarked, and there is no claimed real-world accuracy percentage. A reconciled balance does not prove all transactions are present or descriptions/names are right. Extremely faint/cut-off scans, handwriting, complex multi-account pages, and unreadable text can remain unresolved even after automatic retries and AI. Automated completion means the pipeline finishes without row-by-row work, not a guarantee of error-free data. Printed English statements are the primary target.

To rebuild the optional local AI bundle, first create `work/ai-env` with Homebrew Python 3.13, install `mlx-vlm==0.7.8`, and run `work/ai-env/bin/python setup_ai.py`. This downloads public weights only, never statement images. Model revision and source are recorded in the bundled manifest. Documents are processed with Hugging Face/Transformers offline mode enabled. Model documentation: [GLM-OCR MLX implementation](https://github.com/Blaizzy/mlx-vlm/tree/main/mlx_vlm/models/glm_ocr), [GLM-OCR 8-bit weights](https://huggingface.co/mlx-community/GLM-OCR-8bit).

OCR quality and segmentation choices follow the [Tesseract quality guidance](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html); worker limits follow [OCRmyPDF performance guidance](https://ocrmypdf.readthedocs.io/en/stable/performance.html). These are free engines, not guarantees about an individual scan.

## License

Application source is MIT licensed; see LICENSE. Tesseract, OCRmyPDF, Poppler, Python, Pillow, openpyxl and Apple's frameworks retain their respective licenses. Bundled package license files are included in their package folders/Third Party Notices where available. Homebrew installs its own dependencies separately.
