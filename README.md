# YouTube AI Agent MVP

Local MVP for producing YouTube-ready video artifacts from a manual idea.

Target flow:

```text
manual_idea.json -> script -> scenes -> assets -> Remotion video -> thumbnail -> seo.json -> report.md
```

The MVP uses deterministic mock providers. It does not use Hermes, YouTube upload, OAuth, Telegram, scheduled publishing, trend research, or real LLM/TTS/image APIs.

## Demo Command

```bash
python3 -m video_agent.cli run --channel configs/vida-plena-45/channel.yaml --idea inputs/manual_idea.json
```

Expected outputs are written under `jobs/<job_id>/`.
