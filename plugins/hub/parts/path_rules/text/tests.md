You are working on tests.
- Prefer a few behaviour tests that run the whole flow over many small unit tests. Mock only at the outer edge, and only what is slow, costly or unsafe to touch.
- Write or change the test before the code, every time, fixes from a review included. Then run it again.
- Check that the test fails when the code is wrong. A test that passes for the wrong reason is worse than none.
- Never make a test pass by weakening it. No skips, no looser assertions, no longer timeouts, and no mocking away the code under test. Fix the code, or fix a test that was itself wrong, and say which.
- A flaky test is a failing test. Find out why it is slow or racy and fix that, instead of running it again.
- Name each scenario as the behaviour it checks, and write given, when and then as named steps.
- When you widen what a shared function takes or returns, check every caller and update that function's own tests too.
