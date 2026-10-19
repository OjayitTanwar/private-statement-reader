# Contribute

Thank you for helping improve Private Statement Reader.

## Protect statement data

Use synthetic documents in issues, tests and pull requests.
Do not submit bank statements, account numbers, names, source crops or exports from real accounts.
Blurring one field is not sufficient. Other fields can identify the account holder.

## Test a change

1. Create a branch.
2. Install the development requirements from the README.
3. Make the change.
4. Run the unit tests and evidence checks.
5. Explain the result in a pull request.

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
node tests/test_evidence.cjs
```

For OCR changes, use `python3 tests/smoke_local.py` on a Mac with the OCR tools installed.
This test creates synthetic statements. It does not use the app library.

## Extraction rules

- Keep the original OCR text.
- Record the source page and method for each estimate.
- Do not describe a balance match as proof of accuracy.
- Do not silently merge people with similar names.
- Keep the processing path local.
- Make failures visible. Do not label the original PDF as searchable if OCR failed.

For interface changes, use the system in [DESIGN.md](DESIGN.md).
Keep source images above uncertain values.

## Report a problem

Include the macOS version, chip, memory size and app version.
Describe the expected result and actual result.
Attach a synthetic example when possible.
Do not paste logs until you remove private paths and financial data.
