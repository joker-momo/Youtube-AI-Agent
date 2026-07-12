# SFX Provenance

Both press sound effects are SELF-GENERATED with ffmpeg sine synthesis — no
sample, recording, or third-party/copyrighted audio is used. Regenerating with
the exact commands below reproduces the same audio content.

## like_pop.wav — two-note rising chime (Like press)

- Fired at the Like press frame (`sfxFrames(fps).likePop == pressFrames(fps).like`).
- Generation command:

```bash
ffmpeg -y -f lavfi -i "aevalsrc=if(lt(t\,0.09)\,0.5*sin(2*PI*1319*t)*exp(-18*t)+0.2*sin(2*PI*2638*t)*exp(-24*t)\,0.55*sin(2*PI*1760*(t-0.09))*exp(-10*(t-0.09))+0.22*sin(2*PI*3520*(t-0.09))*exp(-16*(t-0.09))):s=44100:d=0.45" -ar 44100 -ac 1 like_pop.wav
```

- SHA-256: `5dd34c60d867e1fa24aa1a3433a83c356f70a51e109283135fb74f440f024bad`

## bell_ding.wav — three-partial decaying bell (Subscribe press)

- Fired at the Subscribe press frame (`sfxFrames(fps).bellDing == pressFrames(fps).subscribe`).
- Generation command:

```bash
ffmpeg -y -f lavfi -i "aevalsrc=0.35*sin(2*PI*1319*t)*exp(-6*t)+0.22*sin(2*PI*1976*t)*exp(-8*t)+0.14*sin(2*PI*2637*t)*exp(-10*t):s=44100:d=0.8" -ar 44100 -ac 1 bell_ding.wav
```

- SHA-256: `7c08ac37f5afafafdab631891d939725df0d396d7c38abea13e0ec50de8776d6`

## Contracts

- Durations bounded 0.05–1.5 s (ffprobe-asserted in
  `tests/shorts_build/infographic/test_engagement_cue_timing.py`).
- Final rendered Short audio must be non-clipping (max_volume < 0 dBFS).
