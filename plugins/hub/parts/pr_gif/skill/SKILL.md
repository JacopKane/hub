---
name: hub-pr-gif
description: Records a GIF or screenshot for a pull request body or review comment, checks every frame for private data, uploads it as a GitHub attachment and embeds it with a caption. Use it whenever a PR body or comment needs a GIF or an image, or has to say why none fits.
---

# A GIF in a pull request

A GIF lives only as a GitHub attachment inside the PR body or comment. It is never committed, because a binary in git history stays in every clone of the repo.

## 1. When to use it

Use it for any PR body or comment that needs a GIF or an image. A reviewer takes in a short clip faster than a paragraph. A review comment gets one when it helps the author see the problem.

## 2. Record the real product

- Record the product in use, where a user really sees it, such as a page, a panel, a terminal band or a live flow.
- Record a test run only when the change has nothing a user can see, and say so in the caption.
- Keep it to one short flow of about 10 to 20 seconds. Use invented demo data, such as `Demo User` and `test-token-123`. Never show a real name, account or key, masked or not.
- For a terminal, a recorder that plays a script, such as vhs, works well. Hide the setup steps, run under a throwaway HOME so no real home folder, history or prompt shows, and never print an absolute path. Never use a recorder's own publish command, because it uploads to a public host outside GitHub.
- For a page, record your own browser tab with only the demo data open.

## 3. Check every frame before upload

An upload is permanent. Clearing the comment box does not delete the file, and it stays online under its URL. So check one frame per second, plus the first and the last.

```bash
mkdir <scratch folder>/frames
ffmpeg -loglevel error -i <scratch folder>/demo.gif -vf fps=1 <scratch folder>/frames/second-%04d.png
ffmpeg -loglevel error -i <scratch folder>/demo.gif -frames:v 1 <scratch folder>/frames/first.png
ffmpeg -loglevel error -i <scratch folder>/demo.gif -vf reverse -frames:v 1 <scratch folder>/frames/last.png
```

- Open each frame and look for secrets and tokens, real names, email addresses, private paths such as a home folder, other tabs, notifications and pop-ups.
- If any of them shows, fix the setup, record again and check again. Never upload a frame you have not looked at.
- Delete the frames after the check.

## 4. Keep the file out of the repo

Record into a scratch folder outside the repo and keep the file there. Never `git add` a GIF, and never add a media branch or a GIF folder to a repo. The attachment URL is the only published copy.

## 5. Upload it as a GitHub attachment

The upload is a public step, even with the comment unsent, because the file is online from that moment. So it waits for the user's order together with the other public steps of the work, and for the night hold when hub-pr's night hold runs. An agent hands the file path back instead, unless its brief allows the upload.

1. Open the PR's conversation page on the PR's own repo, so the attachment follows that repo's access. Check that the signed-in GitHub account is the user's.
2. Add the file to the comment box. Drop it in, or use "Paste, drop, or click to add files".
3. Wait until the box holds a tag like `<img ... src="https://github.com/user-attachments/assets/<id>" />`, and copy that tag.
4. Never press Comment. Clear the box and check that it is empty, so the tag is not posted later by accident.
5. With no browser tool, give the user the file path and ask them to drop it into the comment box and copy the tag.

A browser tool that cannot open the system file picker needs a file input. GitHub makes one only when its add-files button is clicked, and a real click opens the picker. Run this once in the tab before the click, so the click only adds a hidden input:

```js
const keep = (input) => { input.id = 'gifinput' + document.querySelectorAll('[id^=gifinput]').length; if (!input.isConnected) document.body.appendChild(input) };
for (const name of ['click', 'showPicker']) { const original = HTMLInputElement.prototype[name]; HTMLInputElement.prototype[name] = function () { return this.type === 'file' ? keep(this) : original.call(this) } }
```

Then click the button from a script, find the newest file input, and upload the file to it. Use a fresh input for each file. A reused one says the upload worked but sends nothing.

## 6. Embed it

- Put the tag in the body or comment with a one-line caption under it that says what it shows, such as "*The status band counts down while a long job runs.*"
- Never write a link to a file that was not uploaded. A made-up attachment link shows as a broken image.
- A screenshot is fine when motion adds nothing.
- When nothing fits, write "No GIF fits, because" and five or more words, such as "No GIF fits, because the change has no screen."
- Read the body back after the update and check that the image is there.
