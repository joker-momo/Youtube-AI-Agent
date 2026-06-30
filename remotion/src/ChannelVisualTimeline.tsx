import React from 'react';
import {AbsoluteFill, Img, Sequence, useVideoConfig} from 'remotion';
import {Video as MediaVideo} from '@remotion/media';
import type {CompiledAssetSchedule} from './render-props';
import {mediaSrc} from './render-props';

// Matches the legacy per-scene background look (ChannelVideo SceneView) so the
// composited frame stays visually consistent when the timeline owns the layer.
const BG_FILTER = 'contrast(1.05) saturate(1.03) sepia(0.04) brightness(0.96)';

// Convert a trim expressed at the source/trim timebase into composition frames.
function normalizedTrimFrame(
  value: number | null | undefined,
  trimTimebaseFps: number | undefined,
  compositionFps: number,
): number | undefined {
  if (!value) return undefined;
  const timebase = trimTimebaseFps && trimTimebaseFps > 0 ? trimTimebaseFps : compositionFps;
  if (!timebase) return value;
  return Math.round((value * compositionFps) / timebase);
}

/**
 * Long-form background layer driven by the compiled asset schedule.
 *
 * Each `background_media` track is a single `<Sequence>` containing a single
 * `<MediaVideo>` (WebCodecs) (or `<Img>`) spanning the whole visual span. Because there
 * is exactly one media element per span — not one per scene — the native clip
 * plays continuously across internal scene boundaries with NO remount and NO
 * playhead reset. Independent of the Shorts VisualTimeline (used as design
 * reference only; not imported).
 */
export const ChannelVisualTimeline: React.FC<{schedule: CompiledAssetSchedule}> = ({schedule}) => {
  const {fps} = useVideoConfig();
  if (schedule.schema_version !== 2) {
    throw new Error(`Unsupported visual schedule schema: ${String(schedule.schema_version)}`);
  }
  return (
    <AbsoluteFill>
      {schedule.tracks.map((track) => {
        if (track.track_type !== 'background_media') return null;
        if (track.loop_policy !== 'forbid') {
          throw new Error(`Unsupported loop policy for ${track.track_id}: ${track.loop_policy}`);
        }
        const src = mediaSrc(track.asset_ref);
        const isVideo = track.render_media_kind === 'video';
        const trimBefore = normalizedTrimFrame(track.trim_before_in_frames, track.trim_timebase_fps, fps);
        const normalizedTrimEnd = normalizedTrimFrame(track.trim_end_in_frames, track.trim_timebase_fps, fps);
        const trimAfter =
          normalizedTrimEnd && (!trimBefore || normalizedTrimEnd > trimBefore) ? normalizedTrimEnd : undefined;
        return (
          <Sequence
            key={track.track_id}
            from={track.from_frame}
            durationInFrames={track.duration_in_frames}
            name={track.visual_span_id}
          >
            {isVideo ? (
              <MediaVideo
                src={src}
                muted
                playbackRate={track.playback_rate}
                trimBefore={trimBefore}
                trimAfter={trimAfter}
                style={{position: 'absolute', width: '100%', height: '100%', objectFit: 'cover', filter: BG_FILTER}}
              />
            ) : (
              <Img
                src={src}
                style={{position: 'absolute', width: '100%', height: '100%', objectFit: 'cover', filter: BG_FILTER}}
              />
            )}
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
