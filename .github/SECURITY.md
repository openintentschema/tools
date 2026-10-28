# Security policy

## Reporting a vulnerability

Please do not open a public issue. Report it privately through GitHub: **Security → Report a
vulnerability** in this repository. Include what you ran, the input that triggers it, and what
you expected.

Relevant here are, for example:

- input that makes an `ois` command crash, hang or read files it should not;
- input that the validator reports as conforming although it breaks a rule, so that forged or
  tampered evidence would pass;
- anything that affects the integrity of the published package or its release workflow.

A weakness in the specification itself, such as a rule that lets forged evidence pass in every
conforming validator, belongs to the
[specification repository](https://github.com/openintentschema/openintentschema/security).

## Supported versions

Fixes go into the latest release on PyPI. During 0.x, older versions are not patched.
