# Stateful CLI property testing

## Purpose

This E2E suite exercises `bin/trans-nix` as a subprocess and checks its persistent
filesystem behavior across generated command sequences. It complements the pure
unit properties and the deterministic executable/library E2E tests. It does not
fuzz malformed command-line strings or re-test package execution.

The primary questions are:

- Is an identical install idempotent without rebuilding its translated root?
- Are package versions and explicit storage slugs isolated from one another?
- Are destination conflicts and `--force` transitions atomic?
- Does `remove` delete the managed root and empty managed parents without
  disturbing other roots?
- Can the CLI recover after a user deletes a destination link or an entire
  translated root?
- Do rejected commands leave the complete test filesystem unchanged?
- Are temporary build, backup, and link paths always cleaned up?

`remove` intentionally owns only the translated root. Destination symlinks may
remain dangling and are the caller's responsibility.

## Isolation and execution

The suite is one `e2e/test_cli_stateful.py` process discovered by the existing
E2E runner. It therefore runs independently in:

- a macOS Seatbelt root;
- an ephemeral x86_64 Linux container;
- an ephemeral aarch64 Linux container.

Every Hypothesis example reuses one short HOME/work root, recursively resets it,
and verifies that no previous example state remains. A response cache and the
Hypothesis example database live beside, not inside, that reset root. The short
HOME keeps relocated roots within the 37-byte byte-length limit.

All CLI calls use the current native platform and the default Nix package output.
`--jobs` remains at its default. Non-native supported platform values appear only
in deliberately mismatched `remove` commands.

## Live response cache

The test starts a localhost reverse proxy with two routes:

- NixHub package metadata;
- `cache.nixos.org` NAR info and archives.

A cache miss is fetched from the real HTTPS service and atomically stored by its
complete upstream URL. Concurrent misses for one URL are serialized. Later
examples receive the exact cached bytes. Production decompressed NAR size and
hash checks remain active.

Package metadata is fetched before Hypothesis starts. This creates a stable input
pool for the run. NAR info and archives are fetched lazily. An upstream failure is
an infrastructure panic that terminates the E2E process; it is not a property
counterexample and must not enter Hypothesis shrinking.

## Generated input pool

The checked-in corpus contains 8–12 small packages that have default outputs on
all three native platforms. Setup fails if any package has no indexed native
versions. The three newest stable versions of each package enter the pool.

Install selectors include:

- exact versions;
- numeric prefixes whose expected resolution is known from the setup inventory;
- `latest`.

The model always records the concrete resolved version.

Storage slugs are generated valid ASCII path segments containing letters, digits,
spaces, dots, dashes, and underscores. They are shortened as needed so the full
relocated root is at most 37 bytes. Repeated values deliberately produce slug
collisions.

Install destinations include:

- absolute paths;
- paths relative to generated working directories;
- `~` paths;
- nested components with spaces, dots, dashes, and Unicode;
- paths beneath symlinked parent directories.

Several logical identities and destinations coexist in each state machine so it
can cover one package, multiple versions, multiple packages, shared roots, shared
destinations, and multiple links to one root.

## State transitions

The general `RuleBasedStateMachine` generates:

- install and identical reinstall;
- install to absent, empty-directory, file, directory, valid-symlink, and
  dangling-symlink destinations;
- install with and without `--force`;
- matching, mismatching, repeated, and forced remove;
- user deletion of a destination;
- user deletion of an entire translated root.

It does not edit manifests or remove arbitrary descendants from closures.
Focused properties guarantee strict identical-install idempotence and complete
managed-root removal in addition to the general machine.

The initial profile is deliberately large: 25 state-machine examples with 10
transitions each on every E2E platform. Environment overrides may reduce these
numbers for local diagnosis. If measured CI time is excessive, a smaller push
profile and larger scheduled profile can be introduced later.

## Oracle

The reference model tracks concrete managed-root identities and destination
nodes. After every transition it checks manifest fields, root placement, link
targets, root isolation, managed-parent cleanup, and the absence of temporary
artifacts.

A structural inventory records every relative path, file type, mode, size, inode,
mtime, and symlink target in the reset root. File contents are not hashed.
Rejected commands must preserve the complete inventory. An identical reinstall
must preserve the translated root's complete inventory, including inode and
mtime, proving that it was reused rather than rebuilt.

When a property fails, the harness prints replayable JSON containing the native
platform, discovered package/version inventory, generated setup, and ordered CLI
and user actions. Standard Hypothesis shrinking and reproduction output remain
available.
