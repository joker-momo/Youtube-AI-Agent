---
name: chatgpt-image-browser
description: Use when an agent must create or retrieve scene images through a user-owned, already logged-in ChatGPT browser session and save downloaded image files into a local project image library.
---

# ChatGPT Image Browser

## Overview

Use a user-controlled Chrome profile with an existing ChatGPT login to create, download, verify, and place images into a project asset folder. Treat this as a supervised browser workflow, not an API integration.

## Preconditions

- A Chrome profile exists for automation, for example `CodeX`.
- The user has already logged in to ChatGPT in that profile.
- The agent has browser automation access to that profile.
- The user has approved the image prompt, or the task explicitly delegates prompt drafting.
- A ChatGPT Project name is known, usually combining channel and video, for example `Vida Plena 45+ - Habitos nocturnos`.
- Project context is known: channel name, audience, video topic, visual rules, and any scene-specific constraints.
- A destination directory is known, for example `inputs/image-library/<job-or-video>/`.

If ChatGPT is not logged in, stop and tell the user. Never type credentials, inspect cookies, read passwords, or attempt to bypass login.

## Workflow

1. Connect to the requested Chrome profile.
2. List open tabs and select a ChatGPT tab if one already exists.
3. If no suitable tab exists, open `https://chatgpt.com/`.
4. Confirm the page is logged in by visible UI state, not by cookies or local storage.
5. Open the `Projects` section in the sidebar.
6. If the target project already exists, open it.
7. If the target project does not exist, create it with the agreed project name.
8. Add or verify a project source/context note before generating images. Use `Sources` -> `Add sources` -> `Text input` when available.
9. Store concise project context in the source note:
   - channel name
   - audience
   - video title/topic
   - visual style rules
   - exclusions such as no text, no logos, no medical claims
10. Return to the project's `Chats` tab.
11. Enter the approved image prompt in the project chat composer.
12. Wait until generation finishes and the image is visible.
13. Open the generated image preview.
14. Click the visible ChatGPT `Download` button in the UI.
15. Locate the newest downloaded image in the user's Downloads folder.
16. Copy it into the project image library using deterministic naming.
17. Verify the copied file type, dimensions, and size.
18. Leave the ChatGPT project tab open only if the user may need to review it; otherwise finalize browser work cleanly.

## Project Context Source

Use a short text source so future image prompts in the same ChatGPT Project inherit the same channel/video context.

Example source title:

```text
Channel and video context
```

Example source body:

```text
Channel: Vida Plena 45+
Audience: Spanish-speaking adults age 45+ interested in practical wellness.
Video: Habitos nocturnos para dormir mejor despues de los 45
Goal: create calm 16:9 YouTube background images for sleep routine scenes.
Visual rules: warm trustworthy editorial photography, no text, no logos, no medical claims, no pills, leave negative space for captions.
```

If the source already exists, do not create duplicates unless the video context changed. Open the existing project and proceed to the project chat.

## Output Contract

Use stable file names that downstream renderers can address without scraping ChatGPT state:

```text
inputs/image-library/<video-or-job-slug>/scene-01.png
inputs/image-library/<video-or-job-slug>/scene-02.png
inputs/image-library/<video-or-job-slug>/thumbnail.png
```

Return a short result containing:

- absolute saved path
- image format
- dimensions
- source profile name
- project name
- whether the project existed or was newly created
- whether a project context source was added or already present
- whether ChatGPT was already logged in
- any manual user action required

## Prompt Pattern

For YouTube scene backgrounds, prefer prompts with:

- exact aspect ratio such as `16:9`
- scene subject and action
- channel audience and tone
- camera/style direction
- negative space requirements for captions
- explicit exclusions: `no text`, `no logos`, `no watermarks`

Example:

```text
Create a 16:9 cinematic YouTube video background image for a Spanish wellness channel named "Vida Plena 45+". Scene: an adult over 45 slowly turning off bright screens one hour before sleep in a calm living room, warm lamp light, soft neutral decor, closed laptop and phone placed away, peaceful bedtime routine, realistic editorial photography, warm trustworthy color palette, no text, no logos, no medical equipment, no pills. Leave clean dark negative space on the left for video captions.
```

## Safety Rules

- Do not automate login.
- Do not use private/internal ChatGPT APIs.
- Do not scrape cookies, tokens, local storage, or account data.
- Do not bulk-generate unattended batches unless the user explicitly approves the operational plan.
- Use the official visible UI and the visible Download button.
- If the page asks for reauthentication, stop and ask the user to handle it.
- If generation fails or rate limits appear, report the exact visible state and stop.

## Verification

Before reporting success:

- Confirm the downloaded file exists.
- Confirm the file is an image with nonzero size.
- Confirm dimensions are suitable for the target renderer.
- Open or render-preview the image when the user needs visual confirmation.
- Keep generated repo changes separate from unapproved pipeline changes.

## Hermes Integration Notes

Expose this skill to Hermes as a human-in-the-loop step with explicit inputs:

```yaml
profile_name: CodeX
chatgpt_url: https://chatgpt.com/
project_name: "Vida Plena 45+ - Habitos nocturnos"
project_context:
  channel: "Vida Plena 45+"
  audience: "Spanish-speaking adults age 45+ interested in practical wellness"
  video: "Habitos nocturnos para dormir mejor despues de los 45"
  visual_rules:
    - "16:9 cinematic YouTube background"
    - "warm trustworthy editorial photography"
    - "no text"
    - "no logos"
    - "no medical claims"
    - "leave negative space for captions"
prompt: "<approved image prompt>"
destination_dir: inputs/image-library/<slug>
output_name: scene-01.png
keep_tab_open: true
```

Hermes should call this only after the script/scenes stage decides what image is needed. The renderer should consume only the saved local image path, never a ChatGPT URL.

## Common Mistakes

| Mistake | Fix |
| --- | --- |
| Treating ChatGPT as an API | Use browser UI only, or switch to an official image API provider. |
| Continuing when logged out | Stop and ask the user to log in. |
| Saving ambiguous filenames | Rename to `scene-XX.png` or another deterministic asset name. |
| Letting renderer depend on Downloads | Copy into the project image library first. |
| Mixing this with unapproved pipeline code | Keep the browser-download skill independent from renderer integration until the flow is approved. |
