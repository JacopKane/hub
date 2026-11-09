You are working on UI. A screen that works but looks unfinished is not done.
- Match the product's design for this screen. When there is none, copy the closest finished screen in the app instead of inventing a layout.
- Reuse the design system's components and tokens. Never build a control by hand that the system already has.
- Line everything up on one grid. Labels, inputs, hints and buttons share edges and baselines, and nothing jumps when a message appears. Check positions in the browser with numbers, not by eye.
- Rows wrap or stack on a narrow screen instead of overflowing. Do not decide a layout with a fixed pixel width.
- Every control has a label, a disabled button says why, and status messages are announced to screen readers.
- Every word a user can read goes through the app's translations.
- Make the loading, empty and error states as finished as the main path.
- Check the change in a real browser before and after it, walking the same path both times. Green tests miss what is only wrong on screen.
