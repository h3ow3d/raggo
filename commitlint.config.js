// Commitlint configuration — Phase 2.
//
// Enforces Conventional Commits on:
//   - the pull-request title (so the squash-merge subject is well-formed)
//   - every commit message in a pull request
//
// Wired up in `.github/workflows/ci.yml` via the `commitlint` job.
// Local enforcement can be added later (e.g. via a Husky `commit-msg`
// hook) but is intentionally out of scope for Phase 2 — CI is the
// gate.

/** @type {import('@commitlint/types').UserConfig} */
module.exports = {
  extends: ["@commitlint/config-conventional"],
  rules: {
    // Conventional Commit types accepted in this repository. This is
    // the standard set plus `chore` and `revert`. Add new types
    // deliberately rather than letting drift creep in.
    "type-enum": [
      2,
      "always",
      [
        "build",
        "chore",
        "ci",
        "docs",
        "feat",
        "fix",
        "perf",
        "refactor",
        "revert",
        "style",
        "test",
      ],
    ],
    // Subjects are free-form prose — don't force a particular case
    // beyond the Conventional Commits header structure itself.
    "subject-case": [0],
    // Allow longer headers than the 72-char default; PR titles often
    // run a bit long once the scope is included and we'd rather lint
    // the structure than the line length.
    "header-max-length": [2, "always", 100],
    // Body / footer wrapping is also relaxed for the same reason.
    "body-max-line-length": [0],
    "footer-max-line-length": [0],
  },
};
