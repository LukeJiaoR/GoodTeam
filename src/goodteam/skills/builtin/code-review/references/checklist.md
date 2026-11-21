# Review Checklist

- SQL/DB: parameterized queries only, no f-string SQL interpolation.
- Subprocess: `shell=False` with list arguments, no unvalidated user input passed as CLI flags.
- File I/O & Archives: resolve paths and verify `.relative_to(base)` to block traversal / Zip Slip.
- Secrets: never log, echo, or serialize API keys/tokens in API responses.
- Tests: verify real edge cases and error paths, not just happy-path mocks.
