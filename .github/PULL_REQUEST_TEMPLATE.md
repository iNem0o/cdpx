## Problem solved

Describe the observable problem, the decision made and its user impact.

## Type of change

- [ ] Bug fix
- [ ] New primitive or feature
- [ ] Documentation or process
- [ ] CI, packaging or maintenance

## CLI contract and CDP protocol

- Impact on stdout JSON, stderr and the 0/1/2 exit codes:
- Expected CDP commands (methods, parameters and order), or a justified `N/A`:

## Tests and documentation

- Tests added or modified:
- Fixture and Chrome/Symfony E2E scenario, or the reason they are `N/A`:
- Documentation (`docs/PRIMITIVES.md`, feature sheet, changelog) updated:

## Security and redaction

- Risks related to cookies, tokens, profiles or session data:
- Redaction measures and verification of the diff/proofs:

## Local validation

List the commands actually run and their result. Clearly flag any check that
was not run and why.

- [ ] `make check-local`
- [ ] `make check` (Docker, real Chrome and Symfony without skips)
- [ ] `make release`

## Automatic GitHub proof

Every PR, including documentation, CI and packaging ones, must obtain the
stable **`PR Gate / Required`** check. The run publishes a native summary and
the cockpit artifact (JSON, HTML, JUnit, logs, captures and available
distributions). A declarative checkbox never replaces this mandatory check.
