# Feature retirement — tasks

The owner approved autonomous implementation of the recorded plan. No external
document/task export. Keep private integration out of core. Use isolated unittest
fixtures and preserve unrelated changes.

- [x] Record requirements and ten initially failing production CLI acceptance cases.
- [x] Implement validated manifest lifecycle, state eligibility, runner and CLI integration.
- [x] Verify bundle detach/attach, reconfigure/uninstall, retry and environment consumers.
- [-] Complete author review and focused regression tests; publish compatible core before bundle adoption.
  Author review completed; 19 lifecycle/boundary tests pass on Mac, the test DS,
  and the replacement test OD. Publication and live adoption remain pending.
