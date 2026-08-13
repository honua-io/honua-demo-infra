# honua-iac caller interface stub

This credential-free test module records the subset of the private
`aws-serverless` module interface consumed by this Terraform root. Its input
names and types are copied from honua-iac commit
`7abdb07c85c7a389a9c3cc97583e534f6c636a3b`, which is also enforced by
`interface-contract.json` and `scripts/validate-terraform-root.py`.

The stub validates root resources, provider schemas, and the pinned module
caller interface. It does not validate the private module implementation.
