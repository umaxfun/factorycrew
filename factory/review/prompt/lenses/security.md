SECURITY. Every candidate carries a threat chain: attacker-controlled entry point -> trust
boundary crossed -> exploit -> concrete impact. Use the trust boundaries in the calibration
file; a chain that crosses none of them is not a candidate unless you show a new one. Check:
authentication and authorisation on every new or changed entry point (positively confirm the
principal; deny-known-bad is fail-open), privilege between roles, injection (SQL, command,
template, path traversal), CORS and CSRF, secrets in code, logs or client bundles, XSS through
unsanitised user content, unbounded input enabling denial of service, message and origin
validation, new dependencies. Also flag an over-tight change that newly refuses a legitimate
operation. No chain means no candidate.
