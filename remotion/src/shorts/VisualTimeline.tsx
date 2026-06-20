import React from 'react';
import {AbsoluteFill, Sequence, useVideoConfig} from 'remotion';
import type {CompiledAssetSchedule} from '../render-props';
import {ShortMediaLayer} from './ShortMediaLayer';

export const VisualTimeline: React.FC<{schedule: CompiledAssetSchedule}> = ({schedule}) => {
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
        return (
          <Sequence
            key={track.track_id}
            from={track.from_frame}
            durationInFrames={track.duration_in_frames}
            name={track.track_id}
          >
            <ShortMediaLayer
              src={track.asset_ref}
              kind={track.render_media_kind}
              cropPlan={track.crop_plan}
              motionPlan={track.motion_plan}
              durationInFrames={track.duration_in_frames}
              trimBeforeInFrames={track.trim_before_in_frames}
              trimEndInFrames={track.trim_end_in_frames}
              trimTimebaseFps={track.trim_timebase_fps}
              compositionFps={fps}
              playbackRate={track.playback_rate}
              legacyStaticDrift={false}
            />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
