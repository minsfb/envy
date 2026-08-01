# Security policy

Please report suspected vulnerabilities privately to the repository maintainer
before opening a public issue. Include the affected version, reproduction steps,
impact, and any suggested mitigation. Do not include real credentials, private
hostnames, personal data, or proprietary files in a report.

The project treats manifest bundles as trusted local code. Only use bundles from
sources you trust and review their handlers, environment values, managed-file
sources, and agent commands before installation.

The optional secrets feature accepts only SOPS documents encrypted to age
recipients. Keep native age identity files restricted to the owning user and do
not commit them. The restored target is a plaintext runtime file with mode
`0600` on Unix. Windows targets inherit the target directory ACL.
